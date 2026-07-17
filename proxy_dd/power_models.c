#include "power_models.h"
#include "proxy.h"
#include <math.h>

double get_power(struct machine_node *machine, double utilization)
{
    if (!machine) {
        return 0.0;
    }

    if (utilization < 0.0) utilization = 0.0;
    if (utilization > 1.0) utilization = 1.0;

    double max_power = machine->max_power;
    double static_power = max_power * machine->static_power_percent;

    if (utilization == 0.0) {
        return static_power;
    }

    switch (machine->power_model_enum) {
        case POWER_MODEL_CUBIC:
            return static_power + (max_power - static_power) * pow(utilization, 3.0);
        case POWER_MODEL_SQUARE:
            return static_power + (max_power - static_power) * pow(utilization, 2.0);
        case POWER_MODEL_SQRT:
            return static_power + (max_power - static_power) * sqrt(utilization);
        case POWER_MODEL_SPEC:
            if (!machine->has_spec_power) {
                return static_power + (max_power - static_power) * utilization;
            }
            if (utilization <= 0.0) return machine->spec_power[0];
            if (utilization >= 1.0) return machine->spec_power[10];
            
            double idx_exact = utilization * 10.0;
            int idx_floor = (int)floor(idx_exact);
            int idx_ceil = (int)ceil(idx_exact);
            
            if (idx_floor == idx_ceil) {
                return machine->spec_power[idx_floor];
            }
            
            double lower_power = machine->spec_power[idx_floor];
            double upper_power = machine->spec_power[idx_ceil];
            double fraction = idx_exact - idx_floor;
            
            return lower_power + (upper_power - lower_power) * fraction;
        case POWER_MODEL_LINEAR:
        default:
            return static_power + (max_power - static_power) * utilization;
    }
}
