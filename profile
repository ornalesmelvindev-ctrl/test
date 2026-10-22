because I have another problem, the way structs were populated, some still schecking foe the variant
static result_code_et read_readouts_data(uint8_t u8_index, uint8_t u8_address, dynamic_readings_st *ps_data)
{
    result_code_et r = RESULT_CODE_ERROR;
    uint16_t u16_buffer[MODBUS_MAX_REGISTER_READ_COUNT] = {0};
    uint16_t u16_data = 0x0000;
    uint16_t u16_startRegister = 0x0000;
    uint8_t u8_registerCount = 0;
    uint16_t u16_registerOffset = 0;

    const device_profile_st *s_device_profile = get_device_profile();

    u16_startRegister = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS;  // 0x3C
    u16_registerOffset = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS; // 0x3C
    u8_registerCount = ((MODBUS_REGISTER_OFFSET_LED_OVERRIDE - MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS) //(0x61 - 0x3C) + 1
                            + MODBUS_REGISTER_OFFSET_LED_OVERRIDE_WORD_SIZE);                                   //(End register - Start register) + End Register Size. Sum is 0x26
    
    if (0 == readModbusRegisters(u8_address,
                                 u16_startRegister,
                                 u8_registerCount,
                                 &u16_buffer[0]))
    {
        /** copy modbus data into register map */
        for (uint8_t i = 0; i < u8_registerCount; i++)
        {
            (void)set_modbus_register(u8_index, (u16_registerOffset + i), u16_buffer[i]);
        }

        /** read outs */
        /** input voltage */
        ps_data->d_inputVoltage = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_VOLTAGE - u16_registerOffset],
                                                         6,
                                                         DATA_FORMAT_UINT16);
        ps_data->d_inputVoltage = limit2Precision(ps_data->d_inputVoltage);

        /** input current */
        ps_data->d_inputCurrent = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_CURRENT - u16_registerOffset],
                                                        10,
                                                        DATA_FORMAT_UINT16);
        ps_data->d_inputCurrent = limit2Precision(ps_data->d_inputCurrent);
        
        /** input ac frequency */
        ps_data->d_inputAcFrequency = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_FREQUENCY - u16_registerOffset],
                                                             0,
                                                             DATA_FORMAT_INT16);
        ps_data->d_inputAcFrequency = limit2Precision(ps_data->d_inputAcFrequency);

        /** current thd */
        ps_data->d_currentThd = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_CURRENT_THD - u16_registerOffset],
                                                       9,
                                                       DATA_FORMAT_INT16);
        ps_data->d_currentThd = limit2Precision(ps_data->d_currentThd);

        /** power factor */
        ps_data->d_powerFactor = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_POWER_FACTOR - u16_registerOffset],
                                                        9,
                                                        DATA_FORMAT_INT16);
        ps_data->d_powerFactor = limit2Precision(ps_data->d_powerFactor);

        /** output voltage */
        ps_data->d_outputVoltage = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_VOLTAGE - u16_registerOffset],
                                                          10,
                                                          DATA_FORMAT_UINT16);
        ps_data->d_outputVoltage = limit2Precision(ps_data->d_outputVoltage);

        /** output current */
        ps_data->d_outputCurrent = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_CURRENT - u16_registerOffset],
                                                          6,
                                                          DATA_FORMAT_UINT16);
        ps_data->d_outputCurrent = limit2Precision(ps_data->d_outputCurrent);

        /** temperatures */
        ps_data->d_inletTemperature = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INLET_TEMPERATURE - u16_registerOffset],
                                                             7,
                                                             DATA_FORMAT_INT16);
        ps_data->d_inletTemperature = limit2Precision(ps_data->d_inletTemperature);

        ps_data->d_outletTemperature = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTLET_TEMPERATURE - u16_registerOffset],
                                                              7,
                                                              DATA_FORMAT_INT16);
        ps_data->d_outletTemperature = limit2Precision(ps_data->d_outletTemperature);

        /** current share */
        ps_data->d_currentShare = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_ISHARE_CURRENT],
                                                         6,
                                                         DATA_FORMAT_INT16);
        ps_data->d_currentShare = limit2Precision(ps_data->d_currentShare);
        
        if (MODULE_VARIANT_DLC != s_device_profile->variant)
        {
            /** fan */
            ps_data->d_fan1Rpm = (double)(u16_buffer[MODBUS_REGISTER_OFFSET_RPM_FAN_0 - u16_registerOffset]);
            ps_data->d_fan1Rpm = limit2Precision(ps_data->d_fan1Rpm);
        }

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
    }

    return r;
}

static result_code_et process_readouts_data_hpr(uint8_t u8_index, uint8_t u8_address, dynamic_readings_st *ps_data)
{
    result_code_et r = RESULT_CODE_ERROR;
    uint16_t u16_buffer[MODBUS_MAX_REGISTER_READ_COUNT] = {0};
    uint16_t u16_data = 0x0000;
    uint16_t u16_startRegister = 0x0000;
    uint8_t u8_registerCount = 0;
    uint16_t u16_registerOffset = 0;
    uint8_t u8_arrayOffset      = 0;

    const device_profile_st *s_device_profile = get_device_profile();

    if ( MODULE_VARIANT_HPR_V2 == s_device_profile->variant )
    {
        u16_startRegister   = MODBUS_REGISTER_OFFSET_RPM_FAN_1; // 0x44
        u16_registerOffset  = MODBUS_REGISTER_OFFSET_RPM_FAN_1; // 0x44
        u8_registerCount    = ((MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE - MODBUS_REGISTER_OFFSET_RPM_FAN_1) //(0x92 - 0x44) + 1
                                + MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE_WORD_SIZE);                       //(End register - Start register) + End Register Size. Sum is 0x4F
    }
    else
    {
        u16_startRegister   = MODBUS_REGISTER_OFFSET_OUTPUT_POWER; // 0x52
        u16_registerOffset  = MODBUS_REGISTER_OFFSET_OUTPUT_POWER; // 0x52
        u8_registerCount    = ((MODBUS_REGISTER_OFFSET_NEG_BUS_BAR_TEMPERATURE - MODBUS_REGISTER_OFFSET_OUTPUT_POWER) //(0x70 - 0x52) + 1
                                + MODBUS_REGISTER_OFFSET_NEG_BUS_BAR_TEMPERATURE_WORD_SIZE);                          //(End register - Start register) + End Register Size. Sum is 0x26
    }

