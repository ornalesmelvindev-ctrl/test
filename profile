wait what have you done?

void std_read_status(uint8_t u8_index, dynamic_status_st *ps_status)
{
    uint16_t u16_ledOverrideBuffer = 0;

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_LED_OVERRIDE, &u16_ledOverrideBuffer))
    {
        setPowerStatusLed(u16_ledOverrideBuffer, ps_status);
    }
}

void std_build_json(json_object *ps_obj, const dynamic_readings_st *ps_data)
{
    json_object_object_add(ps_obj, "fan1_rpm",
                           json_object_new_double(ps_data->d_fan1Rpm));
}

these are not even standard specific only
as you see
only DLC doesnt support those, like
    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_LED_OVERRIDE, &u16_ledOverrideBuffer))
        {
            setPowerStatusLed(u16_ledOverrideBuffer, ps_data);
        }
    }

what???? dont try to mix up things okay?
look here, only tells DLC doesnt support this
        if (MODULE_VARIANT_DLC != s_device_profile->variant)
        {
            /** fan */
            ps_data->d_fan1Rpm = (double)(u16_buffer[MODBUS_REGISTER_OFFSET_RPM_FAN_0 - u16_registerOffset]);
            ps_data->d_fan1Rpm = limit2Precision(ps_data->d_fan1Rpm);
        }

and in here, this is what I am talking about, the first condition is for HPR and HPRv2
then the else part is for standard, that is the only one for standard specific
        if ( (MODULE_VARIANT_HPR == s_device_profile->variant)
                || (MODULE_VARIANT_HPR_V2 == s_device_profile->variant)
        )
        {
            r = process_readouts_data_hpr(u8_index, u8_address, ps_data);
        }
        else
        {
            /** input power */
            ps_data->d_inputPower = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_POWER - u16_registerOffset],
                                                          3,
                                                          DATA_FORMAT_UINT16);
            ps_data->d_inputPower = limit2Precision(ps_data->d_inputPower);

            /** output power */
            ps_data->d_outputPower = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_POWER - u16_registerOffset],
                                                            3,
                                                            DATA_FORMAT_UINT16);
            ps_data->d_outputPower = limit2Precision(ps_data->d_outputPower);

            r = RESULT_CODE_OK;
        }

so maybe like add another file, like variant-common.c, to be called in monitor-dynamic.c  or whereso ever
and ofcourse, files for variant specific only, dont try to mix up things

