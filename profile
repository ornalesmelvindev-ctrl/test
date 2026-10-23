also I think in here
typedef struct variant_ops
{
    /* Decode variant-only readouts. Called after the COMMON block (pu16_buffer,
     * which starts at register u16_base) was read and decoded. May read more
     * register blocks. Return RESULT_CODE_OK on success. */
    result_code_et (*read_dynamic_ext)(uint8_t u8_index, uint8_t u8_address,
                                       const uint16_t *pu16_buffer, uint16_t u16_base,
                                       dynamic_readings_st *ps_data);

    /* Variant-only status decoding (e.g. power LED). */
    void (*read_status_ext)(uint8_t u8_index, dynamic_status_st *ps_status);

    /* Variant-only keys of the dynamic readout json. */
    void (*build_dynamic_ext)(json_object *ps_obj, const dynamic_readings_st *ps_data);
} variant_ops_st;

for it to me more scalable, like these functions is for dynamic_monitor right? So maybe we can do

typedef struct <for monitor dynamic specific functions>

then typedef struct variant_ops
    monitor_dynamic ....
    // then later on if we add for monitor_list or what,
    //

so there's distinction in function initialization?
const variant_ops_st hprv2_ops =
{
    .read_dynamic_ext  = hprv2_read_dynamic_ext,
    .read_status_ext   = hpr_read_status_ext,
    .build_dynamic_ext = hprv2_build_dynamic_ext,
};

what do you think?
