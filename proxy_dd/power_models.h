#ifndef NFR_DAG_POWER_MODELS_H
#define NFR_DAG_POWER_MODELS_H

struct machine_node;

double get_power(const struct machine_node *machine, double utilization);

#endif
