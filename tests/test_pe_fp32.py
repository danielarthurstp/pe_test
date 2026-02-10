# test_fp32_only_pe.py
from __future__ import annotations

import os
import random
import struct
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb_tools.runner import get_runner


# -----------------------------
# FP32 pack/unpack helpers
# -----------------------------
def f32_to_u32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def u32_to_f32(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


def f32_round(x: float) -> float:
    """Round a Python float to IEEE-754 binary32 by pack/unpack."""
    return u32_to_f32(f32_to_u32(x))


def fmt_u32(u: int) -> str:
    return f"0x{u & 0xFFFFFFFF:08x}"


def fmt_u160(u: int) -> str:
    return f"0x{u & ((1 << 160) - 1):040x}"


def pack_5xfp32(vals: list[float]) -> int:
    assert len(vals) == 5
    packed = 0
    for i, v in enumerate(vals):
        packed |= (f32_to_u32(v) & 0xFFFFFFFF) << (32 * i)
    return packed

def f32_from_u32(u: int) -> float:
    return u32_to_f32(u)

def pow2_f32(exp2: int) -> float:
    """Return exactly representable power of two as float32: 2**exp2."""
    return f32_round(2.0 ** exp2)

def lane_vec(*u32s: int) -> list[float]:
    """Build a 5-lane vector from 5 u32 FP32 hex values."""
    assert len(u32s) == 5
    return [f32_from_u32(u) for u in u32s]

# -----------------------------
# Reference model (round only at the end)
# -----------------------------
def dot5_fp32_model(a_vals: list[float], b_vals: list[float]) -> float:
    """
    Compute sum_i (a[i]*b[i]) with *only one FP32 rounding at the end*.

    Since DUT inputs are FP32, we first quantize inputs to FP32 once,
    then do the dot product in Python float (binary64),
    then quantize the final result to FP32 once.
    """
    acc = 0.0
    for a, b in zip(a_vals, b_vals):
        af = f32_round(a)   # input quantization (what packing into FP32 does)
        bf = f32_round(b)
        acc += float(af) * float(bf)
    return f32_round(acc)              # single rounding at the end

# -----------------------------
# Random value generation
# -----------------------------
def rand_f32_normal(
    *,
    small: bool = False,
    large: bool = False,
    allow_inf: bool = False,   # keep for compatibility
) -> float:

    sign = -1.0 if random.random() < 0.5 else 1.0

    if small:
        e = random.randint(-126, -80)
    elif large:
        e = random.randint(20, 60)
    else:
        e = random.randint(-20, 20)

    mant = 1.0 + random.random()
    x = sign * mant * (2.0 ** e)
    return f32_round(x)

def is_f32_finite(x: float) -> bool:
    u = f32_to_u32(x)
    exp = (u >> 23) & 0xFF
    return exp != 0xFF  # excludes inf/NaN

def u32_is_zero(u: int) -> bool:
    return (u & 0x7FFFFFFF) == 0

def sample_vecs_no_overflow(profile: str, max_tries: int = 1000):

    for _ in range(max_tries):
        a = rand_vec5_profile(profile, allow_inf=False)  
        b = rand_vec5_profile(profile, allow_inf=False)

        if not all(is_f32_finite(x) for x in a):
            continue
        if not all(is_f32_finite(x) for x in b):
            continue

        expected = dot5_fp32_model(a, b)

        # Reject if expected would become Inf/NaN in FP32
        if not is_f32_finite(expected):
            continue

        return a, b, expected

    raise RuntimeError(f"Could not sample finite dot product after {max_tries} tries")

def rand_vec5_profile(profile: str, allow_inf: bool) -> list[float]:
    """
    profile: "normal" | "small" | "large" | "mixed"
    """
    if profile == "normal":
        return [rand_f32_normal(allow_inf=allow_inf) for _ in range(5)]
    if profile == "small":
        return [rand_f32_normal(small=True, allow_inf=allow_inf) for _ in range(5)]
    if profile == "large":
        return [rand_f32_normal(large=True, allow_inf=allow_inf) for _ in range(5)]
    if profile == "mixed":
        out = []
        for _ in range(5):
            r = random.random()
            if r < 0.33:
                out.append(rand_f32_normal(small=True, allow_inf=allow_inf))
            elif r < 0.66:
                out.append(rand_f32_normal(large=True, allow_inf=allow_inf))
            else:
                out.append(rand_f32_normal(allow_inf=allow_inf))
        return out
    raise ValueError(f"Unknown profile {profile}")


# -----------------------------
# DUT driver/checker
# -----------------------------
async def drive_pulse_and_check(
    dut,
    a_vals: list[float],
    b_vals: list[float],
    expected: float,
    latency_cycles: int = 4,
    tag: str = "",
):
    assert len(a_vals) == 5 and len(b_vals) == 5

    verbose = os.getenv("VERBOSE", "0") not in ("0", "", "false", "False")

    A_bus = pack_5xfp32(a_vals)
    B_bus = pack_5xfp32(b_vals)

    a_u32 = [f32_to_u32(v) for v in a_vals]
    b_u32 = [f32_to_u32(v) for v in b_vals]
    exp_u32 = f32_to_u32(expected)

    if tag:
        dut._log.info(f"{tag}")
    dut._log.info(f"A_bus={fmt_u160(A_bus)}  B_bus={fmt_u160(B_bus)}")
    if verbose:
        dut._log.info("A lanes: " + ", ".join(f"{fmt_u32(a_u32[i])} ({a_vals[i]})" for i in range(5)))
        dut._log.info("B lanes: " + ", ".join(f"{fmt_u32(b_u32[i])} ({b_vals[i]})" for i in range(5)))

    # Apply inputs and pulse clk_cntr for ONE cycle
    # Apply inputs and run required 2-cycle time-mux
    dut.A.value = A_bus
    dut.B.value = B_bus

    # phase 0
    dut.clk_cntr.value = 0
    await RisingEdge(dut.clk)

    # phase 1
    dut.clk_cntr.value = 1
    await RisingEdge(dut.clk)

    # back to idle
    dut.clk_cntr.value = 0

    # Wait pipeline latency measured from phase-1 edge
    for _ in range(latency_cycles):
        await RisingEdge(dut.clk)

    if not dut.out.value.is_resolvable:
        raise AssertionError(f"out contains X/Z at check time: {str(dut.out.value)}")

    got_u32 = int(dut.out.value.to_unsigned())

    got_f = u32_to_f32(got_u32)

    # Single-line result log
    dut._log.info(f"EXPECT={fmt_u32(exp_u32)}  GOT={fmt_u32(got_u32)}  (exp={expected} got={got_f})")

    if u32_is_zero(got_u32) and u32_is_zero(exp_u32):
        return
    
    assert got_u32 == exp_u32, (
        f"Mismatch:\n"
        f"  A_bus={fmt_u160(A_bus)}\n"
        f"  B_bus={fmt_u160(B_bus)}\n"
        f"  expected={fmt_u32(exp_u32)} ({expected})\n"
        f"  got     ={fmt_u32(got_u32)} ({got_f})\n"
    )

    if verbose:
        await RisingEdge(dut.clk)
        if dut.out.value.is_resolvable:
            got_u32_next = int(dut.out.value.to_unsigned())
            dut._log.info(f"dbg next: out={fmt_u32(got_u32_next)}")
        else:
            dut._log.info("dbg next: out=UNRESOLVABLE")


# -----------------------------
# Tests
# -----------------------------
@cocotb.test()
async def fp32_only_pe_sanity_tests(dut):
    # Init
    dut.A.value = 0
    dut.B.value = 0
    dut.clk_cntr.value = 0

    # Clock
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)

    # Sync
    await RisingEdge(dut.clk)

    # Deterministic sanity tests
    a = [1.0] * 5
    b = [1.0] * 5
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[T1] ones")

    a = [0.0] * 5
    b = [0.0] * 5
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[T2] zeros")

    a = [1.0, 0.0, 1.0, 0.0, 1.0]
    b = [1.0] * 5
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[T3] mixed")

    a = [-1.0] * 5
    b = [1.0] * 5
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[T4] neg")


    # 1.0 * 2.0 = 2.0
    a = lane_vec(0x3f800000, 0, 0, 0, 0)  # [1.0,0,0,0,0]
    b = lane_vec(0x40000000, 0, 0, 0, 0)  # [2.0,0,0,0,0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX1] single mul 1*2")

    # A2) 1.5 * 1.5 = 2.25
    a = lane_vec(0x3fc00000, 0, 0, 0, 0)  # 1.5
    b = lane_vec(0x3fc00000, 0, 0, 0, 0)  # 1.5
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX2] single mul 1.5*1.5")

    # tiny = 2^-40 (exact power of two)
    a = [f32_round(1.0), pow2_f32(-20), 0.0, 0.0, 0.0]  # 2^-20
    b = [f32_round(1.0), pow2_f32(-20), 0.0, 0.0, 0.0]  # 2^-20 
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX3] align big+tiny (2^-40)")

    # C) Cancellation: equal and opposite products should cancel cleanly
    a = [f32_round(2.0), f32_round(-2.0), 0.0, 0.0, 0.0]
    b = [f32_round(3.0), f32_round(3.0),  0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX4] cancellation to zero")

    # C2) Mixed cancellation with 5 lanes: (1*1) + (1*-1) + (1*1) + (1*-1) + (1*1) = 1
    a = [1.0, 1.0, 1.0, 1.0, 1.0]
    b = [1.0, -1.0, 1.0, -1.0, 1.0]
    a = [f32_round(x) for x in a]
    b = [f32_round(x) for x in b]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX5] cancellation pattern -> 1")

    a = [f32_round(1.5), f32_round(1.5), 0.0, 0.0, 0.0]
    b = [f32_round(1.0), f32_round(1.0), 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX6] normalization: 1.5+1.5=3.0")


    a = [f32_round(1.0), pow2_f32(-12), 0.0, 0.0, 0.0]
    b = [f32_round(1.0), pow2_f32(-12), 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX7] rounding tie: 1 + 2^-24")


    a = [f32_round(1.0), pow2_f32(-11), 0.0, 0.0, 0.0]
    b = [f32_round(1.0), pow2_f32(-12), 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX8] rounding step: 1 + 2^-23")

    a = [f32_round(-1.0), -pow2_f32(-12), 0.0, 0.0, 0.0]
    b = [f32_round( 1.0),  pow2_f32(-12), 0.0, 0.0, 0.0]  # product = -2^-24
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[FX9] rounding tie negative")


    # 1) Single-lane multiply (1.0 * 2.0) in each lane
    for lane in range(5):
        a = [0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]
        a[lane] = f32_round(1.0)
        b[lane] = f32_round(2.0)
        await drive_pulse_and_check(
            dut, a, b,
            expected=dot5_fp32_model(a, b),
            latency_cycles=4,
            tag=f"[LANE MUL] lane={lane} 1.0*2.0"
        )

    for lane in range(5):
        a = [0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]

        a[0] = f32_round(1.0)
        b[0] = f32_round(1.0)

        a[lane] = pow2_f32(-12)
        b[lane] = pow2_f32(-12)  # product = 2^-24

        await drive_pulse_and_check(
            dut, a, b,
            expected=dot5_fp32_model(a, b),
            latency_cycles=4,
            tag=f"[LANE TIE] tiny in lane={lane}  1 + 2^-24"
        )

    for lane in range(5):
        a = [0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]

        a[0] = f32_round(1.0)
        b[0] = f32_round(1.0)

        a[lane] = pow2_f32(-20)
        b[lane] = pow2_f32(-20)

        await drive_pulse_and_check(
            dut, a, b,
            expected=dot5_fp32_model(a, b),
            latency_cycles=4,
            tag=f"[LANE ALIGN] tiny in lane={lane}  1 + 2^-40"
        )

    for lane in range(1, 5):
        a = [0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]

        a[0] = f32_round(2.0)
        b[0] = f32_round(3.0)

        a[lane] = f32_round(-2.0)
        b[lane] = f32_round(3.0)

        await drive_pulse_and_check(
            dut, a, b,
            expected=dot5_fp32_model(a, b),
            latency_cycles=4,
            tag=f"[LANE CANCEL] lanes=(0,{lane})  (+6)+(-6)=0"
        )

    for lane in range(1, 5):
        a = [0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]

        a[0] = f32_round(1.5)
        b[0] = f32_round(1.5)

        a[lane] = f32_round(-1.5)
        b[lane] = f32_round(1.5)

        await drive_pulse_and_check(
            dut, a, b,
            expected=dot5_fp32_model(a, b),
            latency_cycles=4,
            tag=f"[LANE CANCEL2] lanes=(0,{lane})  (+2.25)+(-2.25)=0"
        )



    a = [f32_round(16000.02013), f32_round(16000.02013), f32_round(16000.02013), f32_round(16000.02013), f32_round(16000.02013)]
    b = [f32_round(1.0),         f32_round(0.0),         f32_round(0.0),         f32_round(0.0),         f32_round(0.0)]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG1] sum bigs: 5*16000.02013")


    a = [f32_round(16000.02013), 0.0, 0.0, 0.0, 0.0]
    b = [f32_round(40.0), 0.0, 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG1.5] single-lane big mul: ")


    a = [f32_round(16000.02013), 0.0, 0.0, 0.0, 0.0]
    b = [f32_round(40.02013), 0.0, 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG1.6] single-lane big mul: ")


    a = [f32_round(16000.02013), 0.0, 0.0, 0.0, 0.0]
    b = [f32_round(16000.02013), 0.0, 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG2] single-lane big mul: 16000.02013^2")

    a = [f32_round(16000.02013), f32_round(8000.0101), f32_round(4000.005), f32_round(2000.0025), f32_round(1000.00125)]
    b = [f32_round(1.0),         f32_round(2.0),       f32_round(4.0),      f32_round(8.0),       f32_round(16.0)]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG3] mixed magnitudes + scaling")


    a = [f32_round(16000.02013), pow2_f32(-10), 0.0, 0.0, 0.0]
    b = [f32_round(1.0),         pow2_f32(-10), 0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG4] big+tiny: 16000.02013 + 2^-20")


    a = [f32_round(16000.02013), f32_round(-16000.02013), 0.0, 0.0, 0.0]
    b = [f32_round(2.0),         f32_round(2.0),          0.0, 0.0, 0.0]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG5] big cancellation -> 0")

    a = [f32_round(16000.02013), f32_round(-12000.015), f32_round(8000.01), f32_round(-4000.005), f32_round(2000.0025)]
    b = [f32_round(1.25),        f32_round(1.50),       f32_round(1.75),    f32_round(2.00),      f32_round(2.25)]
    await drive_pulse_and_check(dut, a, b, expected=dot5_fp32_model(a, b), latency_cycles=4, tag="[BIG6] mixed signs big dot")

@cocotb.test()
async def fp32_only_pe_random_tests(dut):
    # Init
    dut.A.value = 0
    dut.B.value = 0
    dut.clk_cntr.value = 0

    # Clock
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)

    await RisingEdge(dut.clk)

    seed = int(os.getenv("SEED", "12345"))
    random.seed(seed)
    dut._log.info(f"[RND] seed={seed}")

    ntests = int(os.getenv("RND_TESTS", "25"))
    allow_inf = os.getenv("ALLOW_INF", "0") not in ("0", "", "false", "False")

    profiles = ["normal", "small", "large", "mixed"]

    for t in range(ntests):
        prof = profiles[t % len(profiles)]
        a = rand_vec5_profile(prof, allow_inf=allow_inf)
        b = rand_vec5_profile(prof, allow_inf=allow_inf)

        expected = dot5_fp32_model(a, b)

        await drive_pulse_and_check(
            dut,
            a,
            b,
            expected=expected,
            latency_cycles=4,
            tag=f"[RND {t+1:03d}/{ntests}] profile={prof}",
        )


# -----------------------------
# Runner
# -----------------------------
def test_fp32_only_pe_runner():
    sim = os.getenv("SIM", "icarus")

    proj_path = Path(__file__).resolve().parent.parent
    sources = [
        proj_path / "golden/pe_fp32.sv",
        proj_path / "golden/CEC.v",
        proj_path / "golden/Alignment_Shifter.v",
        proj_path / "golden/Adder_Tree.v",
        proj_path / "golden/CLA_AdderTree.v",
        proj_path / "golden/compressor7to2.v",
        proj_path / "golden/csla.v",
        proj_path / "golden/LZD.v",
        proj_path / "golden/multi12bX12b.v",
    ]

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="pe_fp32",
        always=True,
    )

    runner.test(
        hdl_toplevel="pe_fp32",
        test_module="test_pe_fp32",
    )