// FINN's stitched IP has no TLAST, and the DMA needs one to finish a transfer.
// BEATS = output bytes per inference.
module tlast_gen #(
    parameter integer BEATS = 1
) (
    input  wire       aclk,
    input  wire       aresetn,

    input  wire [7:0] s_axis_tdata,
    input  wire       s_axis_tvalid,
    output wire       s_axis_tready,

    output wire [7:0] m_axis_tdata,
    output wire       m_axis_tvalid,
    input  wire       m_axis_tready,
    output wire       m_axis_tlast
);

    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tvalid = s_axis_tvalid;
    assign s_axis_tready = m_axis_tready;

    generate
        if (BEATS <= 1) begin : single_beat
            assign m_axis_tlast = 1'b1;
        end else begin : counted
            reg [31:0] cnt = 32'd0;
            assign m_axis_tlast = (cnt == BEATS - 1);
            always @(posedge aclk) begin
                if (!aresetn)                                cnt <= 32'd0;
                else if (s_axis_tvalid && m_axis_tready)     cnt <= m_axis_tlast ? 32'd0 : cnt + 32'd1;
            end
        end
    endgenerate

endmodule
