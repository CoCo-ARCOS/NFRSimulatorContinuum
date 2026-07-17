#ifndef POWER_MODELS_H
#define POWER_MODELS_H

#ifdef __cplusplus
extern "C" {
#endif

enum power_model_type {
    POWER_MODEL_LINEAR = 0,
    POWER_MODEL_CUBIC,
    POWER_MODEL_SQUARE,
    POWER_MODEL_SQRT,
    POWER_MODEL_COUNT
};

/* Forward declaration so we don't have to include proxy.h if we don't want to */
struct machine_node;

/**
 * @brief Computes power based on utilization (0.0 to 1.0) for a given machine's power model.
 * 
 * @param machine Pointer to the machine_node configuration
 * @param utilization CPU utilization fraction [0.0, 1.0]
 * @return Power in Watts
 */
double get_power(struct machine_node *machine, double utilization);

#ifdef __cplusplus
}
#endif

#endif /* POWER_MODELS_H */
