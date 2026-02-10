    `timescale 1ns / 1ps

    module pe_fp32(
        input  [159:0] A,
        input  [159:0] B,
        input          clk,
        input          clk_cntr,
        output reg [31:0] out
    );

        // -----------------------------
        // FP32 lane extraction registers
        // -----------------------------
        reg [22:0] mant_A32[0:4];
        reg [22:0] mant_B32[0:4];
        reg [7:0]  exp_A32 [0:4];
        reg [7:0]  exp_B32 [0:4];
        reg        sign_A32[0:4];
        reg        sign_B32[0:4];

        //
        localparam [1:0] FP32_MODE = 2'b01;
        reg [1:0] mode_p1, mode_p2, mode_p3;

        reg clk_cntr_stage1;
        reg clk_cntr_stage2;
        reg clk_cntr_stage3;

        // -----------------------------
        // Stage 1 signals (mantissa mult, exp compare, alignment shift)
        // -----------------------------
        reg  [11:0] a0, a1, a2, a3, a4, a5, a6, a7, a8, a9;
        reg  [11:0] b0, b1, b2, b3, b4, b5, b6, b7, b8, b9;
        reg  [11:0] c6, c7, c8, c9;
        reg         mode_m1, mode_m2;

        wire [23:0] pp0, pp1, pp2, pp3, pp4, pp5, pp6, pp7, pp8, pp9;

        reg  [9:0] exp_A_0, exp_A_1, exp_A_2, exp_A_3, exp_A_4, exp_A_5, exp_A_6, exp_A_7, exp_A_8, exp_A_9;
        reg  [9:0] exp_B_0, exp_B_1, exp_B_2, exp_B_3, exp_B_4, exp_B_5, exp_B_6, exp_B_7, exp_B_8, exp_B_9;

        reg  [9:0] mmax_exp;
        wire [9:0] max_exp;
        wire [9:0] diff_0, diff_1, diff_2, diff_3, diff_4, diff_5, diff_6, diff_7, diff_8, diff_9;

        wire [10:0] exp64_unused;
        wire [10:0] exp_A64_tieoff = 11'b0;
        wire [10:0] exp_B64_tieoff = 11'b0;

        reg  sign0, sign1, sign2, sign3, sign4;
        reg  ssign0, ssign1, ssign2, ssign3, ssign4;
        // Remaining signs are forced 0 in FP32 mode
        wire [60:0] was_out[0:9];
        reg  [60:0] aas_out[0:9];

        // -----------------------------
        // Stage 2 signals (2's comp, adder tree, accumulation)
        // -----------------------------
        reg  [5:0] p_shift[0:9];
        reg  [7:0] d_ddiff[0:9];
        reg        s_sign[0:9];

        wire [60:0] as_out[0:9];

        reg  [63:0] n1, n2, n3, n4, n5, n6, n7, n8, n9, n10;
        reg  [65:0] acc_in;
        wire [65:0] sum;
        wire        signf;

        reg  [65:0] ssum;
        reg         ssignf;
        reg  [9:0]  mmax_exp_s2;

        // -----------------------------
        // Stage 3 signals (normalize + pack)
        // -----------------------------
        wire [6:0]  position;
        reg  [65:0] inter;
        reg  [31:0] outtt;
        reg  [31:0] outt;
        reg         carry_rounder;
        wire [65:0] sum_f;

        // -----------------------------
        // Instances
        // -----------------------------
        multi12bX12b m0(a0, b0, pp0);
        multi12bX12b m1(a1, b1, pp1);
        multi12bX12b m2(a2, b2, pp2);
        multi12bX12b m3(a3, b3, pp3);
        multi12bX12b m4(a4, b4, pp4);
        multi12bX12b m5(a5, b5, pp5);
        multi12bX12b m6(a6, b6, pp6);
        multi12bX12b m7(a7, b7, pp7);
        multi12bX12b m8(a8, b8, pp8);
        multi12bX12b m9(a9, b9, pp9);

        CEC ec1(
            exp_A_0, exp_A_1, exp_A_2, exp_A_3, exp_A_4,
            exp_A_5, exp_A_6, exp_A_7, exp_A_8, exp_A_9,
            exp_B_0, exp_B_1, exp_B_2, exp_B_3, exp_B_4,
            exp_B_5, exp_B_6, exp_B_7, exp_B_8, exp_B_9,
            max_exp,
            diff_0, diff_1, diff_2, diff_3, diff_4,
            diff_5, diff_6, diff_7, diff_8, diff_9
        );

        Alignment_Shifter as0(pp0, d_ddiff[0], p_shift[0], was_out[0]);
        Alignment_Shifter as1(pp1, d_ddiff[1], p_shift[1], was_out[1]);
        Alignment_Shifter as2(pp2, d_ddiff[2], p_shift[2], was_out[2]);
        Alignment_Shifter as3(pp3, d_ddiff[3], p_shift[3], was_out[3]);
        Alignment_Shifter as4(pp4, d_ddiff[4], p_shift[4], was_out[4]);
        Alignment_Shifter as5(pp5, d_ddiff[5], p_shift[5], was_out[5]);
        Alignment_Shifter as6(pp6, d_ddiff[6], p_shift[6], was_out[6]);
        Alignment_Shifter as7(pp7, d_ddiff[7], p_shift[7], was_out[7]);
        Alignment_Shifter as8(pp8, d_ddiff[8], p_shift[8], was_out[8]);
        Alignment_Shifter as9(pp9, d_ddiff[9], p_shift[9], was_out[9]);


        complement2ss1 c2s0(aas_out[0], s_sign[0], as_out[0]);
        complement2ss1 c2s1(aas_out[1], s_sign[1], as_out[1]);
        complement2ss1 c2s2(aas_out[2], s_sign[2], as_out[2]);
        complement2ss1 c2s3(aas_out[3], s_sign[3], as_out[3]);
        complement2ss1 c2s4(aas_out[4], s_sign[4], as_out[4]);
        complement2ss1 c2s5(aas_out[5], s_sign[5], as_out[5]);
        complement2ss1 c2s6(aas_out[6], s_sign[6], as_out[6]);
        complement2ss1 c2s7(aas_out[7], s_sign[7], as_out[7]);
        complement2ss1 c2s8(aas_out[8], s_sign[8], as_out[8]);
        complement2ss1 c2s9(aas_out[9], s_sign[9], as_out[9]);

        Adder_Tree AT1(
            n1[62:0], n2[62:0], n3[62:0], n4[62:0],
            n7[62:0], n8[62:0], n9[62:0], n10[62:0],
            n5, n6,
            acc_in,
            sum,
            signf
        );
        

        data_selector ii(ssum, sum_f);
        LZD z1(sum_f, position);

        // -----------------------------
        // Sequential pipeline registers
        // -----------------------------
        integer i;
        always @(posedge clk) begin
            // FP32 lane extraction (5 lanes)
            mant_A32[0] <= A[22:0];    exp_A32[0] <= A[30:23];   sign_A32[0] <= A[31];
            mant_A32[1] <= A[54:32];   exp_A32[1] <= A[62:55];   sign_A32[1] <= A[63];
            mant_A32[2] <= A[86:64];   exp_A32[2] <= A[94:87];   sign_A32[2] <= A[95];
            mant_A32[3] <= A[118:96];  exp_A32[3] <= A[126:119]; sign_A32[3] <= A[127];
            mant_A32[4] <= A[150:128]; exp_A32[4] <= A[158:151]; sign_A32[4] <= A[159];

            mant_B32[0] <= B[22:0];    exp_B32[0] <= B[30:23];   sign_B32[0] <= B[31];
            mant_B32[1] <= B[54:32];   exp_B32[1] <= B[62:55];   sign_B32[1] <= B[63];
            mant_B32[2] <= B[86:64];   exp_B32[2] <= B[94:87];   sign_B32[2] <= B[95];
            mant_B32[3] <= B[118:96];  exp_B32[3] <= B[126:119]; sign_B32[3] <= B[127];
            mant_B32[4] <= B[150:128]; exp_B32[4] <= B[158:151]; sign_B32[4] <= B[159];

            // Force FP32 mode through pipeline
            mode_p1 <= FP32_MODE;
            mode_p2 <= FP32_MODE;
            mode_p3 <= FP32_MODE;

            // Pipeline time-mux control
            clk_cntr_stage1 <= clk_cntr;
            clk_cntr_stage2 <= clk_cntr_stage1;
            clk_cntr_stage3 <= clk_cntr_stage2;

            // Stage 1 registered outputs
            mmax_exp <= max_exp;

            ssign0 <= sign0;
            ssign1 <= sign1;
            ssign2 <= sign2;
            ssign3 <= sign3;
            ssign4 <= sign4;

            for (i = 0; i < 10; i = i + 1) begin
                aas_out[i] <= was_out[i];
            end

            // Stage 2 registered outputs
            ssum        <= sum;
            ssignf      <= signf;
            mmax_exp_s2 <= mmax_exp;

            // Stage 3 registered output
            out <= outt;
        end

        // -----------------------------
        // Stage 1 combinational (FP32 only)
        // -----------------------------
        always @(*) begin
            // Default (avoid inferred latches)
            a0 = 12'b0; a1 = 12'b0; a2 = 12'b0; a3 = 12'b0; a4 = 12'b0;
            a5 = 12'b0; a6 = 12'b0; a7 = 12'b0; a8 = 12'b0; a9 = 12'b0;
            b0 = 12'b0; b1 = 12'b0; b2 = 12'b0; b3 = 12'b0; b4 = 12'b0;
            b5 = 12'b0; b6 = 12'b0; b7 = 12'b0; b8 = 12'b0; b9 = 12'b0;
            c6 = 12'b0; c7 = 12'b0; c8 = 12'b0; c9 = 12'b0;
            mode_m1 = 1'b0; mode_m2 = 1'b0;

            // Build 24-bit mantissas (implicit leading 1 when non-zero exp/mant)
            if (clk_cntr_stage1 == 1'b0) begin
                {a1, a0} = (mant_A32[0] | {15'b0, exp_A32[0]}) ? {1'b1, mant_A32[0]} : 24'b0;
                {a3, a2} = (mant_A32[1] | {15'b0, exp_A32[1]}) ? {1'b1, mant_A32[1]} : 24'b0;
                {a5, a4} = (mant_A32[2] | {15'b0, exp_A32[2]}) ? {1'b1, mant_A32[2]} : 24'b0;
                {a7, a6} = (mant_A32[3] | {15'b0, exp_A32[3]}) ? {1'b1, mant_A32[3]} : 24'b0;
                {a9, a8} = (mant_A32[4] | {15'b0, exp_A32[4]}) ? {1'b1, mant_A32[4]} : 24'b0;

                // lower 12b chunk of B mantissa
                b0 = mant_B32[0][11:0]; b1 = mant_B32[0][11:0];
                b2 = mant_B32[1][11:0]; b3 = mant_B32[1][11:0];
                b4 = mant_B32[2][11:0]; b5 = mant_B32[2][11:0];

                b6 = mant_B32[3][11:0]; b7 = mant_B32[3][11:0];
                b8 = mant_B32[4][11:0]; b9 = mant_B32[4][11:0];
            end else begin
                {a1, a0} = (mant_A32[0] | {15'b0, exp_A32[0]}) ? {1'b1, mant_A32[0]} : 24'b0;
                {a3, a2} = (mant_A32[1] | {15'b0, exp_A32[1]}) ? {1'b1, mant_A32[1]} : 24'b0;
                {a5, a4} = (mant_A32[2] | {15'b0, exp_A32[2]}) ? {1'b1, mant_A32[2]} : 24'b0;
                {a7, a6} = (mant_A32[3] | {15'b0, exp_A32[3]}) ? {1'b1, mant_A32[3]} : 24'b0;
                {a9, a8} = (mant_A32[4] | {15'b0, exp_A32[4]}) ? {1'b1, mant_A32[4]} : 24'b0;

                // upper 11 bits + implicit 1 of B mantissa when non-zero
                b0 = (mant_B32[0] | {15'b0, exp_B32[0]}) ? {1'b1, mant_B32[0][22:12]} : 12'b0;
                b1 = (mant_B32[0] | {15'b0, exp_B32[0]}) ? {1'b1, mant_B32[0][22:12]} : 12'b0;
                b2 = (mant_B32[1] | {15'b0, exp_B32[1]}) ? {1'b1, mant_B32[1][22:12]} : 12'b0;
                b3 = (mant_B32[1] | {15'b0, exp_B32[1]}) ? {1'b1, mant_B32[1][22:12]} : 12'b0;
                b4 = (mant_B32[2] | {15'b0, exp_B32[2]}) ? {1'b1, mant_B32[2][22:12]} : 12'b0;
                b5 = (mant_B32[2] | {15'b0, exp_B32[2]}) ? {1'b1, mant_B32[2][22:12]} : 12'b0;

                b6 = (mant_B32[3] | {15'b0, exp_B32[3]}) ? {1'b1, mant_B32[3][22:12]} : 12'b0;
                b7 = (mant_B32[3] | {15'b0, exp_B32[3]}) ? {1'b1, mant_B32[3][22:12]} : 12'b0;
                b8 = (mant_B32[4] | {15'b0, exp_B32[4]}) ? {1'b1, mant_B32[4][22:12]} : 12'b0;
                b9 = (mant_B32[4] | {15'b0, exp_B32[4]}) ? {1'b1, mant_B32[4][22:12]} : 12'b0;
            end

            // Exponents to CEC
            exp_A_0 = exp_A32[0]; exp_B_0 = exp_B32[0];
            exp_A_1 = exp_A32[1]; exp_B_1 = exp_B32[1];
            exp_A_2 = exp_A32[2]; exp_B_2 = exp_B32[2];
            exp_A_3 = exp_A32[3]; exp_B_3 = exp_B32[3];
            exp_A_4 = exp_A32[4]; exp_B_4 = exp_B32[4];
            exp_A_5 = 10'b0;      exp_B_5 = 10'b0;
            exp_A_6 = 10'b0;      exp_B_6 = 10'b0;
            exp_A_7 = 10'b0;      exp_B_7 = 10'b0;
            exp_A_8 = 10'b0;      exp_B_8 = 10'b0;
            exp_A_9 = 10'b0;      exp_B_9 = 10'b0;

            // Sign XOR per FP32 lane
            sign0 = sign_A32[0] ^ sign_B32[0];
            sign1 = sign_A32[1] ^ sign_B32[1];
            sign2 = sign_A32[2] ^ sign_B32[2];
            sign3 = sign_A32[3] ^ sign_B32[3];
            sign4 = sign_A32[4] ^ sign_B32[4];

            // Alignment shift setup
            if(clk_cntr_stage1 == 1'b0)
            begin
                p_shift[0] = 6'b0;                  d_ddiff[0] = diff_0;               
                p_shift[1] = 6'd12;                 d_ddiff[1] = diff_0;              
                p_shift[2] = 6'b0;                  d_ddiff[2] = diff_1;              
                p_shift[3] = 6'd12;                 d_ddiff[3] = diff_1;               
                p_shift[4] = 6'b0;                  d_ddiff[4] = diff_2;              
                p_shift[5] = 6'd12;                 d_ddiff[5] = diff_2;               
                p_shift[6] = 6'b0;                  d_ddiff[6] = diff_3;               
                p_shift[7] = 6'd12;                 d_ddiff[7] = diff_3;               
                p_shift[8] = 6'b0;                  d_ddiff[8] = diff_4;              
                p_shift[9] = 6'd12;                 d_ddiff[9] = diff_4;               
            end
            else
            begin   
                p_shift[0] = 6'd12;                 d_ddiff[0] = diff_0;               
                p_shift[1] = 6'd24;                 d_ddiff[1] = diff_0;              
                p_shift[2] = 6'd12;                 d_ddiff[2] = diff_1;              
                p_shift[3] = 6'd24;                 d_ddiff[3] = diff_1;               
                p_shift[4] = 6'd12;                 d_ddiff[4] = diff_2;              
                p_shift[5] = 6'd24;                 d_ddiff[5] = diff_2;              
                p_shift[6] = 6'd12;                 d_ddiff[6] = diff_3;              
                p_shift[7] = 6'd24;                 d_ddiff[7] = diff_3;               
                p_shift[8] = 6'd12;                 d_ddiff[8] = diff_4;              
                p_shift[9] = 6'd24;                 d_ddiff[9] = diff_4;               
            end
        end

        // -----------------------------
        // Stage 2 combinational
        // -----------------------------
        always @(*) begin
            integer k;

            s_sign[0] = ssign0; s_sign[1] = ssign0;
            s_sign[2] = ssign1; s_sign[3] = ssign1;
            s_sign[4] = ssign2; s_sign[5] = ssign2;
            s_sign[6] = ssign3; s_sign[7] = ssign3;
            s_sign[8] = ssign4; s_sign[9] = ssign4;

            n1  = {as_out[0][60], as_out[0][60], as_out[0][60], as_out[0]};
            n2  = {as_out[1][60], as_out[1][60], as_out[1][60], as_out[1]};
            n3  = {as_out[2][60], as_out[2][60], as_out[2][60], as_out[2]};
            n4  = {as_out[3][60], as_out[3][60], as_out[3][60], as_out[3]};
            n5  = {as_out[4][60], as_out[4][60], as_out[4][60], as_out[4]};
            n6  = {as_out[5][60], as_out[5][60], as_out[5][60], as_out[5]};
            n7  = {as_out[6][60], as_out[6][60], as_out[6][60], as_out[6]};
            n8  = {as_out[7][60], as_out[7][60], as_out[7][60], as_out[7]};
            n9  = {as_out[8][60], as_out[8][60], as_out[8][60], as_out[8]};
            n10 = {as_out[9][60], as_out[9][60], as_out[9][60], as_out[9]};

            if (clk_cntr_stage2 == 1'b0)
                acc_in = 66'b0;
            else
                acc_in = ssum;
        end

        // -----------------------------
        // Stage 3
        // -----------------------------
        reg [22:0] mant_keep;
        reg        G, R, S;
        reg        lsb;
        reg        inc;
        reg [23:0] mant_rounded;  // 1 extra bit for carry

        always @(*) begin
        if (clk_cntr_stage3 == 1'b0) begin
            inter = 66'b0;
            outt  = 32'b0;
        end else begin
            inter = sum_f << position;

            // Keep 23 bits (fraction field)
            mant_keep = inter[65:43];

            // Guard/round/sticky
            G = inter[42];
            R = inter[41];
            S = |inter[40:0];

            lsb = mant_keep[0];
            inc = G & (R | S | lsb);   // RNE

            mant_rounded = {1'b0, mant_keep} + inc;

            if (mant_rounded[23]) begin
            outt[22:0] = mant_rounded[23:1];  // shift right 1

            end else begin
            outt[22:0] = mant_rounded[22:0];
            end

            if (sum_f == 0) begin
            outt[30:23] = 0;
            end else if (position >= 20) begin
            outt[30:23] = mmax_exp_s2 - (position - 20);
            end else begin
            outt[30:23] = mmax_exp_s2 + (20 - position);
            end

            if (mant_rounded[23] && sum_f != 0)
            outt[30:23] = outt[30:23] + 1;

            outt[31] = ssignf;
        end
        end

    endmodule
