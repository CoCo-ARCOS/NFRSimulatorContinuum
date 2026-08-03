#ifndef NFR_DAG_SERVICE_TIME_H
#define NFR_DAG_SERVICE_TIME_H

#include <stddef.h>

#include "proxy.h"

struct service_prediction {
    double mean_time_s;
    double stddev_time_s;
    double ratio;
    int extrapolated;
};

int service_time_init(const struct config *configuration,
                      const char *runtime_base_dir,
                      char *error_buffer,
                      size_t error_buffer_size);

int service_time_predict(int machine_index,
                         enum nfr_type type,
                         int inverse,
                         const struct nfr_operation *operation,
                         double logical_size_bytes,
                         struct service_prediction *prediction,
                         char *error_buffer,
                         size_t error_buffer_size);

void service_time_shutdown(void);

#endif
