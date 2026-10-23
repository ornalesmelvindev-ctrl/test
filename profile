I think you're missing something
because these are the device profiles

typedef enum
{
    MODULE_VARIANT_STANDARD = 0,
    MODULE_VARIANT_HPR      = 1,
    MODULE_VARIANT_HPR_V2   = 2,
    MODULE_VARIANT_DLC      = 3
} module_variant_et;

I mean in variant-dlc.c
because this else part is for standard and dlc, but maybe it's a bit confusing naming it as that only? need to change something?
if so, please, also, dont remove the existing comments from the original implementation, like the // address or everythin
