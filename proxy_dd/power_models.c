#include "power_models.h"
#include "proxy.h"

#include <math.h>

static double clamp01(double value)
{
    if (value < 0.0) return 0.0;
    if (value > 1.0) return 1.0;
    return value;
}

double get_power(const struct machine_node *machine, double utilization)
{
    if (!machine || machine->max_power < 0.0)
        return 0.0;

    utilization = clamp01(utilization);

    /* SPEC supplies absolute watts at 0%, 10%, ..., 100%. It must be
       evaluated before the generic idle-power branch. */
    if (machine->power_model_enum == POWER_MODEL_SPEC && machine->has_spec_power)
    {
        if (utilization <= 0.0)
            return fmax(0.0, machine->spec_power[0]);
        if (utilization >= 1.0)
            return fmax(0.0, machine->spec_power[10]);

        double exact = utilization * 10.0;
        int lower = (int)floor(exact);
        int upper = lower + 1;
        double fraction = exact - (double)lower;
        double watts = machine->spec_power[lower] +
                       (machine->spec_power[upper] - machine->spec_power[lower]) * fraction;
        return fmax(0.0, watts);
    }

    double static_fraction = clamp01(machine->static_power_percent);
    double static_power = machine->max_power * static_fraction;
    double dynamic_power = machine->max_power - static_power;

    switch (machine->power_model_enum)
    {
    case POWER_MODEL_CUBIC:
        return static_power + dynamic_power * pow(utilization, 3.0);
    case POWER_MODEL_SQUARE:
        return static_power + dynamic_power * pow(utilization, 2.0);
    case POWER_MODEL_SQRT:
        return static_power + dynamic_power * sqrt(utilization);
    case POWER_MODEL_SPEC:
        /* Invalid/missing SPEC curve: deterministic linear fallback. */
        return static_power + dynamic_power * utilization;
    case POWER_MODEL_LINEAR:
    default:
        return static_power + dynamic_power * utilization;
    }
}
