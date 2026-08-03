#ifndef NFR_DAG_PROXY_H
#define NFR_DAG_PROXY_H

#include <stddef.h>
#include <stdint.h>

#define MAX_TASKS 256
#define MAX_EDGES 1024
#define MAX_MACHINES 128
#define MAX_LINKS 512
#define MAX_PIPELINE_TASKS 16
#define MAX_NAME_LEN 96
#define MAX_PATH_LEN 4096

#ifdef __cplusplus
extern "C" {
#endif

enum power_model_type {
    POWER_MODEL_LINEAR = 0,
    POWER_MODEL_SQUARE,
    POWER_MODEL_CUBIC,
    POWER_MODEL_SQRT,
    POWER_MODEL_SPEC
};

enum nfr_type {
    NFR_NONE = 0,
    NFR_COMPRESS,
    NFR_ENCRYPT,
    NFR_ERASURE,
    NFR_HASH,
    NFR_COUNT
};

struct nfr_operation {
    enum nfr_type type;
    char algorithm[32];
    int k;
    int m;
    int key_bits;

    /* Optional explicit model. A zero throughput means: use calibration CSVs. */
    double encode_throughput_Bps;
    double decode_throughput_Bps;
    double fixed_overhead_s;
    double decode_fixed_overhead_s;
    double ratio;
    uint64_t metadata_bytes;
};

struct dag_task {
    char id[MAX_NAME_LEN];
    int machine_index;
    double service_time_s;
    double source_input_size_factor;
    double output_size_factor;
    double read_bandwidth_Bps;
    double write_bandwidth_Bps;
    int indegree;
    int outdegree;
};

struct dag_edge {
    char id[MAX_NAME_LEN];
    int source_task;
    int target_task;
    double size_factor;
    int pipeline_count;
    struct nfr_operation pipeline[MAX_PIPELINE_TASKS];
};

struct machine_node {
    char name[MAX_NAME_LEN];
    char hardware_profile[MAX_NAME_LEN];
    char real_values_dir[MAX_PATH_LEN];

    int slots;
    double speed_factor;
    double read_bandwidth_Bps;
    double write_bandwidth_Bps;

    char power_model[32];
    enum power_model_type power_model_enum;
    double max_power;
    double static_power_percent;
    int has_spec_power;
    double spec_power[11];
};

struct link_node {
    char id[MAX_NAME_LEN];
    char from[MAX_NAME_LEN];
    char to[MAX_NAME_LEN];
    int from_machine;
    int to_machine;
    int bidirectional;
    int slots;
    double bandwidth_Bps;
    double latency_s;
    double energy_per_byte;
};

struct workload_config {
    int instances;
    char arrival_model[32];
    double mean_interarrival_s;
    double input_size_bytes;
    double input_size_cv;
};

struct config {
    int schema_version;
    uint64_t seed;
    char config_hash[80];
    char service_time_model[32];
    char real_values_dir[MAX_PATH_LEN];
    int strict_calibration;
    int allow_extrapolation;

    struct workload_config workload;

    int tasks_number;
    struct dag_task tasks[MAX_TASKS];

    int edges_number;
    struct dag_edge edges[MAX_EDGES];

    int machines_number;
    struct machine_node machines[MAX_MACHINES];

    int links_number;
    struct link_node links[MAX_LINKS];
};

struct machine_result {
    char name[MAX_NAME_LEN];
    double busy_slot_seconds;
    double active_seconds;
    double average_utilization;
    double energy_j;
};

struct link_result {
    char id[MAX_NAME_LEN];
    char from[MAX_NAME_LEN];
    char to[MAX_NAME_LEN];
    uint64_t transfers;
    double bytes;
    double busy_slot_seconds;
    double energy_j;
};

struct task_result {
    char id[MAX_NAME_LEN];
    uint64_t executions;
    double total_duration_s;
    double mean_duration_s;
    double first_start_s;
    double last_finish_s;
};

struct simulation_result {
    char status[16];
    char error[512];
    uint64_t seed;
    char config_hash[80];

    int instances_expected;
    int instances_completed;
    double first_arrival_s;
    double last_completion_s;
    double makespan_s;

    int extrapolated_predictions;
    int missing_predictions;

    int machines_number;
    struct machine_result machines[MAX_MACHINES];
    double network_energy_j;
    double total_energy_j;

    int links_number;
    struct link_result links[MAX_LINKS];

    int tasks_number;
    struct task_result tasks[MAX_TASKS];
};

struct config *read_config(const char *file_name, char *error_buffer, size_t error_buffer_size);
int validate_config(struct config *configuration, char *error_buffer, size_t error_buffer_size);
int run_dag_simulation(const struct config *configuration,
                       struct simulation_result *result,
                       char *error_buffer,
                       size_t error_buffer_size);
int write_simulation_results(const struct config *configuration,
                             const struct simulation_result *result,
                             const char *output_dir,
                             char *error_buffer,
                             size_t error_buffer_size);
void free_config(struct config *configuration);
const char *nfr_type_name(enum nfr_type type);

#ifdef __cplusplus
}
#endif

#endif
