#define _POSIX_C_SOURCE 200809L

#include "proxy.h"
#include "power_models.h"
#include "service_time.h"

#include <errno.h>
#include <float.h>
#include <json-c/json.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ------------------------------------------------------------------------- */
/* Configuration parsing                                                     */
/* ------------------------------------------------------------------------- */

static void set_error(char *buffer, size_t size, const char *message)
{
    if (!buffer || size == 0)
        return;
    snprintf(buffer, size, "%s", message ? message : "unknown simulator error");
}

static void set_errorf(char *buffer, size_t size, const char *format,
                       const char *a, const char *b)
{
    if (!buffer || size == 0)
        return;
    snprintf(buffer, size, format, a ? a : "", b ? b : "");
}

static void copy_string(char *destination, size_t destination_size, const char *source)
{
    if (!destination || destination_size == 0)
        return;
    destination[0] = '\0';
    if (source)
        snprintf(destination, destination_size, "%s", source);
}

static int json_get_object(json_object *parent, const char *key, json_object **value)
{
    return parent && key && value && json_object_object_get_ex(parent, key, value);
}

static const char *json_get_string_default(json_object *parent,
                                           const char *key,
                                           const char *default_value)
{
    json_object *value = NULL;
    if (json_get_object(parent, key, &value) &&
        json_object_is_type(value, json_type_string))
        return json_object_get_string(value);
    return default_value;
}

static double json_get_double_default(json_object *parent,
                                      const char *key,
                                      double default_value)
{
    json_object *value = NULL;
    if (json_get_object(parent, key, &value) &&
        (json_object_is_type(value, json_type_double) ||
         json_object_is_type(value, json_type_int)))
        return json_object_get_double(value);
    return default_value;
}

static int json_get_int_default(json_object *parent, const char *key, int default_value)
{
    json_object *value = NULL;
    if (json_get_object(parent, key, &value) &&
        json_object_is_type(value, json_type_int))
        return json_object_get_int(value);
    return default_value;
}

static int json_get_bool_default(json_object *parent, const char *key, int default_value)
{
    json_object *value = NULL;
    if (json_get_object(parent, key, &value) &&
        json_object_is_type(value, json_type_boolean))
        return json_object_get_boolean(value) ? 1 : 0;
    return default_value;
}

static double json_get_bandwidth(json_object *object,
                                 const char *bytes_key,
                                 const char *mb_key,
                                 double default_value)
{
    json_object *value = NULL;
    if (json_get_object(object, bytes_key, &value) &&
        (json_object_is_type(value, json_type_double) ||
         json_object_is_type(value, json_type_int)))
        return json_object_get_double(value);
    if (json_get_object(object, mb_key, &value) &&
        (json_object_is_type(value, json_type_double) ||
         json_object_is_type(value, json_type_int)))
        return json_object_get_double(value) * 1048576.0;
    return default_value;
}

static int machine_index_by_name(const struct config *configuration, const char *name)
{
    if (!configuration || !name)
        return -1;
    for (int index = 0; index < configuration->machines_number; ++index)
        if (strcmp(configuration->machines[index].name, name) == 0)
            return index;
    return -1;
}

static int task_index_by_id(const struct config *configuration, const char *id)
{
    if (!configuration || !id)
        return -1;
    for (int index = 0; index < configuration->tasks_number; ++index)
        if (strcmp(configuration->tasks[index].id, id) == 0)
            return index;
    return -1;
}

static int is_power_model_name(const char *name)
{
    return name &&
           (strcasecmp(name, "linear") == 0 ||
            strcasecmp(name, "square") == 0 ||
            strcasecmp(name, "cubic") == 0 ||
            strcasecmp(name, "sqrt") == 0 ||
            strcasecmp(name, "square-root") == 0 ||
            strcasecmp(name, "spec") == 0);
}

static enum power_model_type parse_power_model(const char *name)
{
    if (!name)
        return POWER_MODEL_LINEAR;
    if (strcasecmp(name, "square") == 0)
        return POWER_MODEL_SQUARE;
    if (strcasecmp(name, "cubic") == 0)
        return POWER_MODEL_CUBIC;
    if (strcasecmp(name, "sqrt") == 0 || strcasecmp(name, "square-root") == 0)
        return POWER_MODEL_SQRT;
    if (strcasecmp(name, "spec") == 0)
        return POWER_MODEL_SPEC;
    return POWER_MODEL_LINEAR;
}

static enum nfr_type parse_nfr_type(const char *name)
{
    if (!name)
        return NFR_NONE;
    if (strcasecmp(name, "compress") == 0 || strcasecmp(name, "compression") == 0)
        return NFR_COMPRESS;
    if (strcasecmp(name, "encrypt") == 0 || strcasecmp(name, "cipher") == 0 ||
        strcasecmp(name, "confidentiality") == 0)
        return NFR_ENCRYPT;
    if (strcasecmp(name, "erasure") == 0 || strcasecmp(name, "reliability") == 0 ||
        strcasecmp(name, "ida") == 0 || strcasecmp(name, "reed-solomon") == 0)
        return NFR_ERASURE;
    if (strcasecmp(name, "hash") == 0 || strcasecmp(name, "integrity") == 0)
        return NFR_HASH;
    return NFR_NONE;
}

const char *nfr_type_name(enum nfr_type type)
{
    switch (type)
    {
    case NFR_COMPRESS: return "compress";
    case NFR_ENCRYPT: return "encrypt";
    case NFR_ERASURE: return "erasure";
    case NFR_HASH: return "hash";
    case NFR_NONE:
    default: return "none";
    }
}

static int parse_operation(json_object *object,
                           struct nfr_operation *operation,
                           char *error_buffer,
                           size_t error_buffer_size)
{
    const char *type_name;
    const char *algorithm;

    if (!object || !operation || !json_object_is_type(object, json_type_object))
    {
        set_error(error_buffer, error_buffer_size, "every edge pipeline item must be an object");
        return -1;
    }

    memset(operation, 0, sizeof(*operation));
    type_name = json_get_string_default(object, "type", NULL);
    operation->type = parse_nfr_type(type_name);
    if (operation->type == NFR_NONE)
    {
        set_errorf(error_buffer, error_buffer_size,
                   "unknown NFR operation type '%s'%s", type_name, "");
        return -1;
    }

    algorithm = json_get_string_default(object, "algorithm",
                 json_get_string_default(object, "algo", NULL));
    if (!algorithm || algorithm[0] == '\0')
    {
        set_error(error_buffer, error_buffer_size, "an NFR pipeline operation is missing algorithm");
        return -1;
    }
    copy_string(operation->algorithm, sizeof(operation->algorithm), algorithm);

    operation->k = json_get_int_default(object, "k",
                   json_get_int_default(object, "ida_k", 0));
    operation->m = json_get_int_default(object, "m",
                   json_get_int_default(object, "ida_m", 0));
    operation->key_bits = json_get_int_default(object, "key_bits",
                          json_get_int_default(object, "aes_key_bits", 256));

    operation->encode_throughput_Bps =
        json_get_double_default(object, "encode_throughput_Bps",
        json_get_double_default(object, "throughput_Bps", 0.0));
    operation->decode_throughput_Bps =
        json_get_double_default(object, "decode_throughput_Bps", 0.0);
    operation->fixed_overhead_s =
        json_get_double_default(object, "fixed_overhead_s", 0.0);
    operation->decode_fixed_overhead_s =
        json_get_double_default(object, "decode_fixed_overhead_s", 0.0);
    operation->ratio = json_get_double_default(object, "ratio", 0.0);

    double metadata = json_get_double_default(object, "metadata_bytes", 0.0);
    operation->metadata_bytes = metadata > 0.0 ? (uint64_t)llround(metadata) : 0;

    return 0;
}

static int parse_machines(json_object *infrastructure,
                          struct config *configuration,
                          char *error_buffer,
                          size_t error_buffer_size)
{
    json_object *machines = NULL;

    if (!json_get_object(infrastructure, "machines", &machines) ||
        !json_object_is_type(machines, json_type_array))
    {
        set_error(error_buffer, error_buffer_size, "infrastructure.machines must be an array");
        return -1;
    }

    size_t count = json_object_array_length(machines);
    if (count == 0 || count > MAX_MACHINES)
    {
        set_error(error_buffer, error_buffer_size, "invalid number of continuum machines");
        return -1;
    }

    configuration->machines_number = (int)count;
    for (size_t index = 0; index < count; ++index)
    {
        json_object *object = json_object_array_get_idx(machines, index);
        struct machine_node *machine = &configuration->machines[index];
        const char *name;
        const char *model;
        json_object *spec = NULL;

        if (!object || !json_object_is_type(object, json_type_object))
        {
            set_error(error_buffer, error_buffer_size, "each machine must be an object");
            return -1;
        }

        memset(machine, 0, sizeof(*machine));
        name = json_get_string_default(object, "name", NULL);
        if (!name || name[0] == '\0')
        {
            set_error(error_buffer, error_buffer_size, "each machine needs a non-empty name");
            return -1;
        }
        copy_string(machine->name, sizeof(machine->name), name);
        copy_string(machine->hardware_profile, sizeof(machine->hardware_profile),
                    json_get_string_default(object, "hardware_profile",
                    json_get_string_default(object, "profile", "default")));
        copy_string(machine->real_values_dir, sizeof(machine->real_values_dir),
                    json_get_string_default(object, "real_values_dir", ""));

        machine->slots = json_get_int_default(object, "slots",
                         json_get_int_default(object, "cores", 1));
        machine->speed_factor = json_get_double_default(object, "speed_factor", 1.0);
        double common_bw = json_get_bandwidth(object, "b_fs_bytes_per_sec", "b_fs", 100.0 * 1048576.0);
        machine->read_bandwidth_Bps = json_get_bandwidth(object,
                                      "read_bandwidth_Bps", "b_fs_read", common_bw);
        machine->write_bandwidth_Bps = json_get_bandwidth(object,
                                       "write_bandwidth_Bps", "b_fs_write", common_bw);

        model = json_get_string_default(object, "power_model", "linear");
        if (!is_power_model_name(model))
        {
            set_errorf(error_buffer, error_buffer_size,
                       "unknown power model '%s'%s", model, "");
            return -1;
        }
        copy_string(machine->power_model, sizeof(machine->power_model), model);
        machine->power_model_enum = parse_power_model(model);
        machine->max_power = json_get_double_default(object, "max_power_w",
                             json_get_double_default(object, "max_power", 150.0));
        machine->static_power_percent =
            json_get_double_default(object, "static_power_fraction",
            json_get_double_default(object, "static_power_percent", 0.5));

        if (json_get_object(object, "spec_power", &spec) &&
            json_object_is_type(spec, json_type_array) &&
            json_object_array_length(spec) == 11)
        {
            machine->has_spec_power = 1;
            for (int point = 0; point < 11; ++point)
                machine->spec_power[point] =
                    json_object_get_double(json_object_array_get_idx(spec, (size_t)point));
        }
    }
    return 0;
}

static int parse_links(json_object *infrastructure,
                       struct config *configuration,
                       char *error_buffer,
                       size_t error_buffer_size)
{
    json_object *links = NULL;
    if (!json_get_object(infrastructure, "links", &links))
    {
        configuration->links_number = 0;
        return 0;
    }
    if (!json_object_is_type(links, json_type_array))
    {
        set_error(error_buffer, error_buffer_size, "infrastructure.links must be an array");
        return -1;
    }

    size_t count = json_object_array_length(links);
    if (count > MAX_LINKS)
    {
        set_error(error_buffer, error_buffer_size, "too many continuum network links");
        return -1;
    }

    configuration->links_number = (int)count;
    for (size_t index = 0; index < count; ++index)
    {
        json_object *object = json_object_array_get_idx(links, index);
        struct link_node *link = &configuration->links[index];
        const char *from;
        const char *to;

        if (!object || !json_object_is_type(object, json_type_object))
        {
            set_error(error_buffer, error_buffer_size, "each network link must be an object");
            return -1;
        }
        memset(link, 0, sizeof(*link));
        from = json_get_string_default(object, "from", NULL);
        to = json_get_string_default(object, "to", NULL);
        if (!from || !to)
        {
            set_error(error_buffer, error_buffer_size, "every network link needs from and to");
            return -1;
        }
        copy_string(link->from, sizeof(link->from), from);
        copy_string(link->to, sizeof(link->to), to);
        const char *id = json_get_string_default(object, "id", NULL);
        if (id)
            copy_string(link->id, sizeof(link->id), id);
        else
            snprintf(link->id, sizeof(link->id), "%s->%s", from, to);

        link->bidirectional = json_get_bool_default(object, "bidirectional", 1);
        link->slots = json_get_int_default(object, "slots", 1);
        link->bandwidth_Bps = json_get_bandwidth(object, "bandwidth_Bps", "b_net", 0.0);
        link->latency_s = json_get_double_default(object, "latency_s",
                          json_get_double_default(object, "latency_ms", 0.0) / 1000.0);
        link->energy_per_byte = json_get_double_default(object, "energy_per_byte_j",
                                json_get_double_default(object, "energy_per_byte", 0.0));
    }
    return 0;
}

static int parse_tasks(json_object *workflow,
                       struct config *configuration,
                       char *error_buffer,
                       size_t error_buffer_size)
{
    json_object *tasks = NULL;
    if (!json_get_object(workflow, "tasks", &tasks) ||
        !json_object_is_type(tasks, json_type_array))
    {
        set_error(error_buffer, error_buffer_size, "workflow.tasks must be an array");
        return -1;
    }

    size_t count = json_object_array_length(tasks);
    if (count == 0 || count > MAX_TASKS)
    {
        set_error(error_buffer, error_buffer_size, "invalid number of DAG tasks");
        return -1;
    }

    configuration->tasks_number = (int)count;
    for (size_t index = 0; index < count; ++index)
    {
        json_object *object = json_object_array_get_idx(tasks, index);
        struct dag_task *task = &configuration->tasks[index];
        const char *id;
        const char *machine_name;

        if (!object || !json_object_is_type(object, json_type_object))
        {
            set_error(error_buffer, error_buffer_size, "each DAG task must be an object");
            return -1;
        }
        memset(task, 0, sizeof(*task));
        id = json_get_string_default(object, "id",
             json_get_string_default(object, "name", NULL));
        machine_name = json_get_string_default(object, "machine", NULL);
        if (!id || !machine_name)
        {
            set_error(error_buffer, error_buffer_size, "every task needs id and machine");
            return -1;
        }
        copy_string(task->id, sizeof(task->id), id);
        task->machine_index = machine_index_by_name(configuration, machine_name);
        if (task->machine_index < 0)
        {
            set_errorf(error_buffer, error_buffer_size,
                       "task refers to unknown machine '%s'%s", machine_name, "");
            return -1;
        }
        task->service_time_s = json_get_double_default(object, "service_time_s",
                               json_get_double_default(object, "service_time", 1.0));
        task->source_input_size_factor =
            json_get_double_default(object, "source_input_size_factor",
            json_get_double_default(object, "input_size_factor", 1.0));
        task->output_size_factor =
            json_get_double_default(object, "output_size_factor",
            json_get_double_default(object, "size_factor", 1.0));
        task->read_bandwidth_Bps =
            json_get_bandwidth(object, "read_bandwidth_Bps", "b_fs_read", 0.0);
        task->write_bandwidth_Bps =
            json_get_bandwidth(object, "write_bandwidth_Bps", "b_fs_write", 0.0);
    }
    return 0;
}

static int parse_edges(json_object *workflow,
                       struct config *configuration,
                       char *error_buffer,
                       size_t error_buffer_size)
{
    json_object *edges = NULL;
    if (!json_get_object(workflow, "edges", &edges) ||
        !json_object_is_type(edges, json_type_array))
    {
        set_error(error_buffer, error_buffer_size, "workflow.edges must be an array (empty is allowed)");
        return -1;
    }

    size_t count = json_object_array_length(edges);
    if (count > MAX_EDGES)
    {
        set_error(error_buffer, error_buffer_size, "too many DAG edges");
        return -1;
    }

    configuration->edges_number = (int)count;
    for (size_t index = 0; index < count; ++index)
    {
        json_object *object = json_object_array_get_idx(edges, index);
        json_object *pipeline = NULL;
        struct dag_edge *edge = &configuration->edges[index];
        const char *source;
        const char *target;
        const char *id;

        if (!object || !json_object_is_type(object, json_type_object))
        {
            set_error(error_buffer, error_buffer_size, "each DAG edge must be an object");
            return -1;
        }
        memset(edge, 0, sizeof(*edge));
        source = json_get_string_default(object, "from",
                 json_get_string_default(object, "source", NULL));
        target = json_get_string_default(object, "to",
                 json_get_string_default(object, "target", NULL));
        if (!source || !target)
        {
            set_error(error_buffer, error_buffer_size, "every edge needs from/source and to/target");
            return -1;
        }
        edge->source_task = task_index_by_id(configuration, source);
        edge->target_task = task_index_by_id(configuration, target);
        if (edge->source_task < 0 || edge->target_task < 0)
        {
            set_error(error_buffer, error_buffer_size, "edge refers to an unknown task");
            return -1;
        }
        id = json_get_string_default(object, "id", NULL);
        if (id)
            copy_string(edge->id, sizeof(edge->id), id);
        else
            snprintf(edge->id, sizeof(edge->id), "%s->%s", source, target);
        edge->size_factor = json_get_double_default(object, "size_factor", 1.0);

        if (json_get_object(object, "pipeline", &pipeline))
        {
            if (!json_object_is_type(pipeline, json_type_array))
            {
                set_error(error_buffer, error_buffer_size, "edge.pipeline must be an array");
                return -1;
            }
            size_t operations = json_object_array_length(pipeline);
            if (operations > MAX_PIPELINE_TASKS)
            {
                set_error(error_buffer, error_buffer_size, "edge NFR pipeline is too long");
                return -1;
            }
            edge->pipeline_count = (int)operations;
            for (size_t operation = 0; operation < operations; ++operation)
            {
                if (parse_operation(json_object_array_get_idx(pipeline, operation),
                                    &edge->pipeline[operation],
                                    error_buffer, error_buffer_size) != 0)
                    return -1;
            }
        }
    }
    return 0;
}

struct config *read_config(const char *file_name,
                           char *error_buffer,
                           size_t error_buffer_size)
{
    json_object *root = NULL;
    json_object *infrastructure = NULL;
    json_object *workflow = NULL;
    json_object *workload = NULL;
    struct config *configuration = NULL;

    if (!file_name)
    {
        set_error(error_buffer, error_buffer_size, "configuration path is null");
        return NULL;
    }

    root = json_object_from_file(file_name);
    if (!root)
    {
        char message[512];
        snprintf(message, sizeof(message), "could not parse JSON configuration '%s'", file_name);
        set_error(error_buffer, error_buffer_size, message);
        return NULL;
    }

    configuration = calloc(1, sizeof(*configuration));
    if (!configuration)
    {
        json_object_put(root);
        set_error(error_buffer, error_buffer_size, "out of memory allocating configuration");
        return NULL;
    }

    configuration->schema_version = json_get_int_default(root, "schema_version", 2);
    json_object *seed_object = NULL;
    if (json_get_object(root, "seed", &seed_object))
    {
        if (!json_object_is_type(seed_object, json_type_int) ||
            json_object_get_int64(seed_object) < 0)
        {
            set_error(error_buffer, error_buffer_size, "seed must be a non-negative integer");
            goto failure;
        }
        configuration->seed = (uint64_t)json_object_get_int64(seed_object);
    }
    else
    {
        configuration->seed = 1;
    }
    copy_string(configuration->config_hash, sizeof(configuration->config_hash),
                json_get_string_default(root, "config_hash", ""));
    copy_string(configuration->service_time_model, sizeof(configuration->service_time_model),
                json_get_string_default(root, "service_time_model", "linear"));
    copy_string(configuration->real_values_dir, sizeof(configuration->real_values_dir),
                json_get_string_default(root, "real_values_dir", ""));
    configuration->strict_calibration = json_get_bool_default(root, "strict_calibration", 1);
    configuration->allow_extrapolation = json_get_bool_default(root, "allow_extrapolation", 0);

    infrastructure = root;
    (void)json_get_object(root, "infrastructure", &infrastructure);
    if (parse_machines(infrastructure, configuration, error_buffer, error_buffer_size) != 0 ||
        parse_links(infrastructure, configuration, error_buffer, error_buffer_size) != 0)
        goto failure;

    if (!json_get_object(root, "workflow", &workflow) ||
        !json_object_is_type(workflow, json_type_object))
    {
        set_error(error_buffer, error_buffer_size, "configuration requires a workflow object");
        goto failure;
    }
    if (parse_tasks(workflow, configuration, error_buffer, error_buffer_size) != 0 ||
        parse_edges(workflow, configuration, error_buffer, error_buffer_size) != 0)
        goto failure;

    workload = NULL;
    (void)json_get_object(root, "workload", &workload);
    if (!workload)
        workload = workflow;
    configuration->workload.instances = json_get_int_default(workload, "instances",
                                        json_get_int_default(workload, "objects", 1));
    copy_string(configuration->workload.arrival_model,
                sizeof(configuration->workload.arrival_model),
                json_get_string_default(workload, "arrival_model", "fixed"));
    configuration->workload.mean_interarrival_s =
        json_get_double_default(workload, "mean_interarrival_s",
        json_get_double_default(workload, "inter_arrival", 0.0));
    configuration->workload.input_size_bytes =
        json_get_double_default(workload, "input_size_bytes",
        json_get_double_default(workload, "size_bytes", 1048576.0));
    configuration->workload.input_size_cv =
        json_get_double_default(workload, "input_size_cv",
        json_get_double_default(workload, "size_cv", 0.0));

    json_object_put(root);
    if (validate_config(configuration, error_buffer, error_buffer_size) != 0)
    {
        free(configuration);
        return NULL;
    }
    return configuration;

failure:
    json_object_put(root);
    free(configuration);
    return NULL;
}

void free_config(struct config *configuration)
{
    free(configuration);
}

/* ------------------------------------------------------------------------- */
/* Validation                                                                */
/* ------------------------------------------------------------------------- */


static int network_path_exists(const struct config *configuration, int source, int target)
{
    int queue[MAX_MACHINES];
    int visited[MAX_MACHINES] = {0};
    int head = 0, tail = 0;

    if (source == target)
        return 1;
    visited[source] = 1;
    queue[tail++] = source;
    while (head < tail)
    {
        int current = queue[head++];
        for (int link_index = 0; link_index < configuration->links_number; ++link_index)
        {
            const struct link_node *link = &configuration->links[link_index];
            int next = -1;
            if (link->from_machine == current)
                next = link->to_machine;
            else if (link->bidirectional && link->to_machine == current)
                next = link->from_machine;
            if (next >= 0 && !visited[next])
            {
                if (next == target)
                    return 1;
                visited[next] = 1;
                queue[tail++] = next;
            }
        }
    }
    return 0;
}

int validate_config(struct config *configuration,
                    char *error_buffer,
                    size_t error_buffer_size)
{
    int indegree[MAX_TASKS] = {0};
    int queue[MAX_TASKS];
    int head = 0, tail = 0, visited_count = 0;

    if (!configuration)
    {
        set_error(error_buffer, error_buffer_size, "configuration is null");
        return -1;
    }
    if (configuration->schema_version != 2)
    {
        set_error(error_buffer, error_buffer_size, "this DAG simulator requires schema_version 2");
        return -1;
    }
    if (configuration->workload.instances <= 0 ||
        !isfinite(configuration->workload.mean_interarrival_s) ||
        configuration->workload.mean_interarrival_s < 0.0 ||
        !isfinite(configuration->workload.input_size_bytes) ||
        configuration->workload.input_size_bytes <= 0.0 ||
        !isfinite(configuration->workload.input_size_cv) ||
        configuration->workload.input_size_cv < 0.0)
    {
        set_error(error_buffer, error_buffer_size, "invalid workload values");
        return -1;
    }
    if (strcasecmp(configuration->workload.arrival_model, "fixed") != 0 &&
        strcasecmp(configuration->workload.arrival_model, "burst") != 0 &&
        strcasecmp(configuration->workload.arrival_model, "exponential") != 0 &&
        strcasecmp(configuration->workload.arrival_model, "poisson") != 0)
    {
        set_error(error_buffer, error_buffer_size,
                  "arrival_model must be fixed, burst, exponential, or poisson");
        return -1;
    }
    if (strcasecmp(configuration->service_time_model, "linear") != 0 &&
        strcasecmp(configuration->service_time_model, "log-log") != 0 &&
        strcasecmp(configuration->service_time_model, "log_log") != 0 &&
        strcasecmp(configuration->service_time_model, "loglog") != 0)
    {
        set_error(error_buffer, error_buffer_size,
                  "service_time_model must be linear or log-log");
        return -1;
    }

    for (int left = 0; left < configuration->machines_number; ++left)
    {
        struct machine_node *machine = &configuration->machines[left];
        if (machine->slots <= 0 || !isfinite(machine->speed_factor) || machine->speed_factor <= 0.0 ||
            !isfinite(machine->read_bandwidth_Bps) || machine->read_bandwidth_Bps <= 0.0 ||
            !isfinite(machine->write_bandwidth_Bps) || machine->write_bandwidth_Bps <= 0.0 ||
            !isfinite(machine->max_power) || machine->max_power < 0.0 ||
            !isfinite(machine->static_power_percent) || machine->static_power_percent < 0.0 ||
            machine->static_power_percent > 1.0)
        {
            set_errorf(error_buffer, error_buffer_size,
                       "invalid capacity, bandwidth, or power values for machine '%s'%s",
                       machine->name, "");
            return -1;
        }
        if (machine->power_model_enum == POWER_MODEL_SPEC)
        {
            if (!machine->has_spec_power)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "SPEC power model requires 11 points for machine '%s'%s",
                           machine->name, "");
                return -1;
            }
            for (int point = 0; point < 11; ++point)
            {
                if (!isfinite(machine->spec_power[point]) || machine->spec_power[point] < 0.0)
                {
                    set_errorf(error_buffer, error_buffer_size,
                               "invalid SPEC power curve for machine '%s'%s",
                               machine->name, "");
                    return -1;
                }
            }
        }
        for (int right = left + 1; right < configuration->machines_number; ++right)
            if (strcmp(machine->name, configuration->machines[right].name) == 0)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "duplicate machine name '%s'%s", machine->name, "");
                return -1;
            }
    }

    for (int link_index = 0; link_index < configuration->links_number; ++link_index)
    {
        struct link_node *link = &configuration->links[link_index];
        link->from_machine = machine_index_by_name(configuration, link->from);
        link->to_machine = machine_index_by_name(configuration, link->to);
        if (link->from_machine < 0 || link->to_machine < 0 ||
            link->from_machine == link->to_machine || link->slots <= 0 ||
            !isfinite(link->bandwidth_Bps) || link->bandwidth_Bps <= 0.0 ||
            !isfinite(link->latency_s) || link->latency_s < 0.0 ||
            !isfinite(link->energy_per_byte) || link->energy_per_byte < 0.0)
        {
            set_errorf(error_buffer, error_buffer_size,
                       "invalid network link '%s'%s", link->id, "");
            return -1;
        }
        for (int other = link_index + 1; other < configuration->links_number; ++other)
        {
            if (strcmp(link->id, configuration->links[other].id) == 0)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "duplicate network link id '%s'%s", link->id, "");
                return -1;
            }
        }
    }

    for (int task = 0; task < configuration->tasks_number; ++task)
    {
        struct dag_task *node = &configuration->tasks[task];
        node->indegree = 0;
        node->outdegree = 0;
        if (node->machine_index < 0 || node->machine_index >= configuration->machines_number ||
            !isfinite(node->service_time_s) || node->service_time_s < 0.0 ||
            !isfinite(node->source_input_size_factor) || node->source_input_size_factor <= 0.0 ||
            !isfinite(node->output_size_factor) || node->output_size_factor <= 0.0)
        {
            set_errorf(error_buffer, error_buffer_size,
                       "invalid DAG task '%s'%s", node->id, "");
            return -1;
        }
        for (int other = task + 1; other < configuration->tasks_number; ++other)
            if (strcmp(node->id, configuration->tasks[other].id) == 0)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "duplicate task id '%s'%s", node->id, "");
                return -1;
            }
    }

    for (int edge_index = 0; edge_index < configuration->edges_number; ++edge_index)
    {
        struct dag_edge *edge = &configuration->edges[edge_index];
        if (edge->source_task < 0 || edge->source_task >= configuration->tasks_number ||
            edge->target_task < 0 || edge->target_task >= configuration->tasks_number ||
            edge->source_task == edge->target_task ||
            !isfinite(edge->size_factor) || edge->size_factor <= 0.0)
        {
            set_errorf(error_buffer, error_buffer_size,
                       "invalid DAG edge '%s'%s", edge->id, "");
            return -1;
        }
        for (int other = edge_index + 1; other < configuration->edges_number; ++other)
            if (strcmp(edge->id, configuration->edges[other].id) == 0)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "duplicate edge id '%s'%s", edge->id, "");
                return -1;
            }

        configuration->tasks[edge->source_task].outdegree++;
        configuration->tasks[edge->target_task].indegree++;
        indegree[edge->target_task]++;

        for (int operation_index = 0;
             operation_index < edge->pipeline_count;
             ++operation_index)
        {
            struct nfr_operation *operation = &edge->pipeline[operation_index];
            if (operation->algorithm[0] == '\0' ||
                !isfinite(operation->encode_throughput_Bps) || operation->encode_throughput_Bps < 0.0 ||
                !isfinite(operation->decode_throughput_Bps) || operation->decode_throughput_Bps < 0.0 ||
                !isfinite(operation->fixed_overhead_s) || operation->fixed_overhead_s < 0.0 ||
                !isfinite(operation->decode_fixed_overhead_s) || operation->decode_fixed_overhead_s < 0.0 ||
                !isfinite(operation->ratio) || operation->ratio < 0.0)
            {
                set_errorf(error_buffer, error_buffer_size,
                           "invalid NFR operation on edge '%s'%s", edge->id, "");
                return -1;
            }
            if (operation->type == NFR_ERASURE &&
                (operation->k <= 0 || operation->m < 0))
            {
                set_errorf(error_buffer, error_buffer_size,
                           "erasure operation requires k > 0 and m >= 0 on edge '%s'%s",
                           edge->id, "");
                return -1;
            }
        }

        int source_machine = configuration->tasks[edge->source_task].machine_index;
        int target_machine = configuration->tasks[edge->target_task].machine_index;
        if (!network_path_exists(configuration, source_machine, target_machine))
        {
            set_errorf(error_buffer, error_buffer_size,
                       "no network route exists for DAG edge '%s'%s", edge->id, "");
            return -1;
        }
    }

    for (int task = 0; task < configuration->tasks_number; ++task)
        if (indegree[task] == 0)
            queue[tail++] = task;
    if (tail == 0)
    {
        set_error(error_buffer, error_buffer_size, "DAG has no source task");
        return -1;
    }

    while (head < tail)
    {
        int task = queue[head++];
        visited_count++;
        for (int edge_index = 0; edge_index < configuration->edges_number; ++edge_index)
        {
            const struct dag_edge *edge = &configuration->edges[edge_index];
            if (edge->source_task == task)
            {
                indegree[edge->target_task]--;
                if (indegree[edge->target_task] == 0)
                    queue[tail++] = edge->target_task;
            }
        }
    }
    if (visited_count != configuration->tasks_number)
    {
        set_error(error_buffer, error_buffer_size, "workflow contains a cycle; a DAG is required");
        return -1;
    }

    return 0;
}

/* ------------------------------------------------------------------------- */
/* Deterministic RNG                                                         */
/* ------------------------------------------------------------------------- */

struct pcg32_state {
    uint64_t state;
    uint64_t increment;
    int has_spare_normal;
    double spare_normal;
};

static uint32_t pcg32_next(struct pcg32_state *rng)
{
    uint64_t old_state = rng->state;
    rng->state = old_state * 6364136223846793005ULL + (rng->increment | 1ULL);
    uint32_t xor_shifted = (uint32_t)(((old_state >> 18u) ^ old_state) >> 27u);
    uint32_t rotation = (uint32_t)(old_state >> 59u);
    return (xor_shifted >> rotation) | (xor_shifted << ((-rotation) & 31));
}

static void pcg32_seed(struct pcg32_state *rng, uint64_t seed)
{
    memset(rng, 0, sizeof(*rng));
    rng->state = 0U;
    rng->increment = (seed << 1u) | 1u;
    (void)pcg32_next(rng);
    rng->state += seed ^ 0x9e3779b97f4a7c15ULL;
    (void)pcg32_next(rng);
}

static double rng_uniform_open(struct pcg32_state *rng)
{
    return ((double)pcg32_next(rng) + 1.0) / 4294967297.0;
}

static double rng_normal(struct pcg32_state *rng)
{
    if (rng->has_spare_normal)
    {
        rng->has_spare_normal = 0;
        return rng->spare_normal;
    }
    double u1 = rng_uniform_open(rng);
    double u2 = rng_uniform_open(rng);
    double radius = sqrt(-2.0 * log(u1));
    double angle = 2.0 * M_PI * u2;
    rng->spare_normal = radius * sin(angle);
    rng->has_spare_normal = 1;
    return radius * cos(angle);
}

/* ------------------------------------------------------------------------- */
/* Event and resource structures                                             */
/* ------------------------------------------------------------------------- */

struct busy_interval {
    double start_s;
    double end_s;
};

struct resource_runtime {
    int slots;
    double *available_at;
    struct busy_interval *intervals;
    size_t interval_count;
    size_t interval_capacity;
    double busy_slot_seconds;
};

enum event_type {
    EVENT_TASK_READY = 0,
    EVENT_TASK_DONE,
    EVENT_EDGE_OPERATION_DONE,
    EVENT_LINK_SEGMENT_DONE
};

struct event {
    double time_s;
    uint64_t sequence;
    enum event_type type;
    int instance;
    int task;
    int edge;
};

struct event_heap {
    struct event *items;
    size_t count;
    size_t capacity;
};

struct task_state {
    int remaining_inputs;
    int scheduled;
    int completed;
    double ready_time_s;
    double input_bytes;
    double output_bytes;
    double start_s;
    double finish_s;
};

enum edge_phase {
    EDGE_PHASE_OUTPUT = 0,
    EDGE_PHASE_TRANSFER,
    EDGE_PHASE_INPUT,
    EDGE_PHASE_DONE
};

struct edge_state {
    int active;
    enum edge_phase phase;
    int operation_index;
    double bytes;
    double size_before[MAX_PIPELINE_TASKS];
    int route[MAX_MACHINES];
    int route_length;
    int route_position;
};

struct simulation_context {
    const struct config *configuration;
    struct simulation_result *result;
    struct pcg32_state rng;

    struct resource_runtime machines[MAX_MACHINES];
    struct resource_runtime links[MAX_LINKS];

    struct task_state *task_states;
    struct edge_state *edge_states;
    int *remaining_sinks;
    double *arrivals;
    double *input_sizes;

    struct event_heap events;
    uint64_t next_sequence;
    int failed;
    char error[512];
};

static size_t task_state_offset(const struct simulation_context *context,
                                int instance,
                                int task)
{
    return (size_t)instance * (size_t)context->configuration->tasks_number + (size_t)task;
}

static size_t edge_state_offset(const struct simulation_context *context,
                                int instance,
                                int edge)
{
    return (size_t)instance * (size_t)context->configuration->edges_number + (size_t)edge;
}

static int resource_init(struct resource_runtime *resource, int slots)
{
    memset(resource, 0, sizeof(*resource));
    resource->slots = slots;
    resource->available_at = calloc((size_t)slots, sizeof(double));
    return resource->available_at ? 0 : -1;
}

static void resource_free(struct resource_runtime *resource)
{
    if (!resource)
        return;
    free(resource->available_at);
    free(resource->intervals);
    memset(resource, 0, sizeof(*resource));
}

static int resource_append_interval(struct resource_runtime *resource,
                                    double start_s,
                                    double end_s)
{
    if (end_s <= start_s)
        return 0;
    if (resource->interval_count == resource->interval_capacity)
    {
        size_t new_capacity = resource->interval_capacity ? resource->interval_capacity * 2 : 256;
        struct busy_interval *new_intervals =
            realloc(resource->intervals, new_capacity * sizeof(*new_intervals));
        if (!new_intervals)
            return -1;
        resource->intervals = new_intervals;
        resource->interval_capacity = new_capacity;
    }
    resource->intervals[resource->interval_count].start_s = start_s;
    resource->intervals[resource->interval_count].end_s = end_s;
    resource->interval_count++;
    resource->busy_slot_seconds += end_s - start_s;
    return 0;
}

static int reserve_resource(struct resource_runtime *resource,
                            double release_s,
                            double duration_s,
                            double *start_s,
                            double *finish_s)
{
    int selected = 0;
    if (!resource || !resource->available_at || resource->slots <= 0 ||
        duration_s < 0.0 || !isfinite(duration_s))
        return -1;

    for (int slot = 1; slot < resource->slots; ++slot)
        if (resource->available_at[slot] < resource->available_at[selected])
            selected = slot;

    *start_s = fmax(release_s, resource->available_at[selected]);
    *finish_s = *start_s + duration_s;
    resource->available_at[selected] = *finish_s;
    return resource_append_interval(resource, *start_s, *finish_s);
}

static int event_less(const struct event *left, const struct event *right)
{
    if (left->time_s < right->time_s)
        return 1;
    if (left->time_s > right->time_s)
        return 0;
    return left->sequence < right->sequence;
}

static int heap_push(struct event_heap *heap, struct event event)
{
    if (heap->count == heap->capacity)
    {
        size_t new_capacity = heap->capacity ? heap->capacity * 2 : 1024;
        struct event *new_items = realloc(heap->items, new_capacity * sizeof(*new_items));
        if (!new_items)
            return -1;
        heap->items = new_items;
        heap->capacity = new_capacity;
    }

    size_t index = heap->count++;
    heap->items[index] = event;
    while (index > 0)
    {
        size_t parent = (index - 1) / 2;
        if (!event_less(&heap->items[index], &heap->items[parent]))
            break;
        struct event temporary = heap->items[index];
        heap->items[index] = heap->items[parent];
        heap->items[parent] = temporary;
        index = parent;
    }
    return 0;
}

static int heap_pop(struct event_heap *heap, struct event *event)
{
    if (!heap || heap->count == 0 || !event)
        return -1;
    *event = heap->items[0];
    heap->count--;
    if (heap->count == 0)
        return 0;
    heap->items[0] = heap->items[heap->count];

    size_t index = 0;
    while (1)
    {
        size_t left = index * 2 + 1;
        size_t right = left + 1;
        size_t smallest = index;
        if (left < heap->count && event_less(&heap->items[left], &heap->items[smallest]))
            smallest = left;
        if (right < heap->count && event_less(&heap->items[right], &heap->items[smallest]))
            smallest = right;
        if (smallest == index)
            break;
        struct event temporary = heap->items[index];
        heap->items[index] = heap->items[smallest];
        heap->items[smallest] = temporary;
        index = smallest;
    }
    return 0;
}

static int push_event(struct simulation_context *context,
                      double time_s,
                      enum event_type type,
                      int instance,
                      int task,
                      int edge)
{
    struct event event;
    event.time_s = time_s;
    event.sequence = context->next_sequence++;
    event.type = type;
    event.instance = instance;
    event.task = task;
    event.edge = edge;
    if (heap_push(&context->events, event) != 0)
    {
        context->failed = 1;
        copy_string(context->error, sizeof(context->error), "out of memory growing event queue");
        return -1;
    }
    return 0;
}

static void context_fail(struct simulation_context *context, const char *message)
{
    if (!context || context->failed)
        return;
    context->failed = 1;
    copy_string(context->error, sizeof(context->error), message);
}

/* ------------------------------------------------------------------------- */
/* Network routing                                                           */
/* ------------------------------------------------------------------------- */

static int link_neighbor(const struct link_node *link, int current)
{
    if (link->from_machine == current)
        return link->to_machine;
    if (link->bidirectional && link->to_machine == current)
        return link->from_machine;
    return -1;
}

static int find_route(const struct config *configuration,
                      int source,
                      int target,
                      double bytes,
                      int *route,
                      int *route_length)
{
    double distance[MAX_MACHINES];
    int visited[MAX_MACHINES] = {0};
    int previous_machine[MAX_MACHINES];
    int previous_link[MAX_MACHINES];

    if (source == target)
    {
        *route_length = 0;
        return 0;
    }

    for (int machine = 0; machine < configuration->machines_number; ++machine)
    {
        distance[machine] = DBL_MAX;
        previous_machine[machine] = -1;
        previous_link[machine] = -1;
    }
    distance[source] = 0.0;

    for (int iteration = 0; iteration < configuration->machines_number; ++iteration)
    {
        int current = -1;
        for (int machine = 0; machine < configuration->machines_number; ++machine)
            if (!visited[machine] && distance[machine] < DBL_MAX &&
                (current < 0 || distance[machine] < distance[current]))
                current = machine;
        if (current < 0)
            break;
        if (current == target)
            break;
        visited[current] = 1;

        for (int link_index = 0; link_index < configuration->links_number; ++link_index)
        {
            const struct link_node *link = &configuration->links[link_index];
            int neighbor = link_neighbor(link, current);
            if (neighbor < 0)
                continue;
            double weight = link->latency_s + bytes / link->bandwidth_Bps;
            double candidate = distance[current] + weight;
            if (candidate < distance[neighbor])
            {
                distance[neighbor] = candidate;
                previous_machine[neighbor] = current;
                previous_link[neighbor] = link_index;
            }
        }
    }

    if (previous_link[target] < 0)
        return -1;

    int reverse[MAX_MACHINES];
    int count = 0;
    for (int current = target; current != source; current = previous_machine[current])
    {
        if (current < 0 || count >= MAX_MACHINES)
            return -1;
        reverse[count++] = previous_link[current];
    }
    for (int index = 0; index < count; ++index)
        route[index] = reverse[count - index - 1];
    *route_length = count;
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Simulation operations                                                     */
/* ------------------------------------------------------------------------- */

static double task_duration(const struct config *configuration,
                            const struct dag_task *task,
                            double input_bytes,
                            double output_bytes)
{
    const struct machine_node *machine = &configuration->machines[task->machine_index];
    double read_bandwidth = task->read_bandwidth_Bps > 0.0 ?
                            task->read_bandwidth_Bps : machine->read_bandwidth_Bps;
    double write_bandwidth = task->write_bandwidth_Bps > 0.0 ?
                             task->write_bandwidth_Bps : machine->write_bandwidth_Bps;
    return task->service_time_s / machine->speed_factor +
           input_bytes / read_bandwidth + output_bytes / write_bandwidth;
}

static int predict_nfr_duration(struct simulation_context *context,
                                int machine_index,
                                const struct nfr_operation *operation,
                                int inverse,
                                double logical_bytes,
                                double input_bytes,
                                double output_bytes,
                                double *duration_s,
                                double *ratio)
{
    struct service_prediction prediction;
    char error[512];
    int status = service_time_predict(machine_index, operation->type, inverse,
                                      operation, logical_bytes, &prediction,
                                      error, sizeof(error));
    if (status != 0)
    {
        context->result->missing_predictions++;
        context_fail(context, error);
        return -1;
    }
    if (prediction.extrapolated)
        context->result->extrapolated_predictions++;

    const struct machine_node *machine = &context->configuration->machines[machine_index];
    *duration_s = prediction.mean_time_s +
                  input_bytes / machine->read_bandwidth_Bps +
                  output_bytes / machine->write_bandwidth_Bps;
    if (ratio)
        *ratio = operation->ratio > 0.0 ? operation->ratio : prediction.ratio;
    if (!isfinite(*duration_s) || *duration_s < 0.0)
    {
        context_fail(context, "NFR model returned an invalid duration");
        return -1;
    }
    return 0;
}

static int schedule_edge_next(struct simulation_context *context,
                              int instance,
                              int edge_index,
                              double release_s);

static int schedule_task(struct simulation_context *context,
                         int instance,
                         int task_index,
                         double release_s)
{
    const struct config *configuration = context->configuration;
    const struct dag_task *task = &configuration->tasks[task_index];
    struct task_state *state =
        &context->task_states[task_state_offset(context, instance, task_index)];
    double start_s, finish_s;

    if (state->scheduled)
        return 0;
    state->scheduled = 1;
    state->output_bytes = fmax(1.0, state->input_bytes * task->output_size_factor);
    double duration_s = task_duration(configuration, task,
                                      state->input_bytes, state->output_bytes);
    if (reserve_resource(&context->machines[task->machine_index], release_s,
                         duration_s, &start_s, &finish_s) != 0)
    {
        context_fail(context, "could not reserve a machine slot for a DAG task");
        return -1;
    }
    state->start_s = start_s;
    state->finish_s = finish_s;
    return push_event(context, finish_s, EVENT_TASK_DONE,
                      instance, task_index, -1);
}

static int begin_edge(struct simulation_context *context,
                      int instance,
                      int edge_index,
                      double release_s,
                      double producer_bytes)
{
    const struct dag_edge *edge = &context->configuration->edges[edge_index];
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];

    memset(state, 0, sizeof(*state));
    state->active = 1;
    state->phase = EDGE_PHASE_OUTPUT;
    state->operation_index = 0;
    state->bytes = fmax(1.0, producer_bytes * edge->size_factor);
    return schedule_edge_next(context, instance, edge_index, release_s);
}

static int complete_edge(struct simulation_context *context,
                         int instance,
                         int edge_index,
                         double completion_s)
{
    const struct dag_edge *edge = &context->configuration->edges[edge_index];
    struct edge_state *edge_state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];
    struct task_state *target_state =
        &context->task_states[task_state_offset(context, instance, edge->target_task)];

    edge_state->phase = EDGE_PHASE_DONE;
    target_state->input_bytes += edge_state->bytes;
    target_state->ready_time_s = fmax(target_state->ready_time_s, completion_s);
    target_state->remaining_inputs--;
    if (target_state->remaining_inputs < 0)
    {
        context_fail(context, "DAG edge completion underflowed task dependency count");
        return -1;
    }
    if (target_state->remaining_inputs == 0)
        return push_event(context, target_state->ready_time_s,
                          EVENT_TASK_READY, instance, edge->target_task, -1);
    return 0;
}

static int schedule_output_operation(struct simulation_context *context,
                                     int instance,
                                     int edge_index,
                                     double release_s)
{
    const struct config *configuration = context->configuration;
    const struct dag_edge *edge = &configuration->edges[edge_index];
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];
    const struct nfr_operation *operation = &edge->pipeline[state->operation_index];
    int machine_index = configuration->tasks[edge->source_task].machine_index;
    double before = state->bytes;
    double after = before;
    double duration_s = 0.0;
    double ratio = 1.0;

    state->size_before[state->operation_index] = before;

    /* Compression needs its calibrated ratio before its output size is known.
       The other transforms have exact size semantics. */
    if (operation->type == NFR_COMPRESS)
    {
        struct service_prediction prediction;
        char error[512];
        if (service_time_predict(machine_index, operation->type, 0, operation,
                                 before, &prediction, error, sizeof(error)) != 0)
        {
            context->result->missing_predictions++;
            context_fail(context, error);
            return -1;
        }
        if (prediction.extrapolated)
            context->result->extrapolated_predictions++;
        ratio = operation->ratio > 0.0 ? operation->ratio : prediction.ratio;
        if (!isfinite(ratio) || ratio <= 0.0)
        {
            context_fail(context, "compression requires a positive calibrated or explicit ratio");
            return -1;
        }
        after = fmax(1.0, before / ratio);
        const struct machine_node *machine = &configuration->machines[machine_index];
        duration_s = prediction.mean_time_s + before / machine->read_bandwidth_Bps +
                     after / machine->write_bandwidth_Bps;
    }
    else
    {
        switch (operation->type)
        {
        case NFR_ENCRYPT:
        case NFR_HASH:
            after = before + (double)operation->metadata_bytes;
            break;
        case NFR_ERASURE:
            after = before * ((double)(operation->k + operation->m) /
                              (double)operation->k) +
                    (double)operation->metadata_bytes;
            break;
        default:
            context_fail(context, "invalid output NFR operation");
            return -1;
        }
        if (predict_nfr_duration(context, machine_index, operation, 0,
                                 before, before, after, &duration_s, NULL) != 0)
            return -1;
    }

    state->bytes = after;
    double start_s, finish_s;
    if (reserve_resource(&context->machines[machine_index], release_s,
                         duration_s, &start_s, &finish_s) != 0)
    {
        context_fail(context, "could not reserve source machine for NFR operation");
        return -1;
    }
    return push_event(context, finish_s, EVENT_EDGE_OPERATION_DONE,
                      instance, -1, edge_index);
}

static int schedule_input_operation(struct simulation_context *context,
                                    int instance,
                                    int edge_index,
                                    double release_s)
{
    const struct config *configuration = context->configuration;
    const struct dag_edge *edge = &configuration->edges[edge_index];
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];
    const struct nfr_operation *operation = &edge->pipeline[state->operation_index];
    int machine_index = configuration->tasks[edge->target_task].machine_index;
    double before = state->bytes;
    double after = state->size_before[state->operation_index];
    double duration_s = 0.0;

    if (predict_nfr_duration(context, machine_index, operation, 1,
                             after, before, after, &duration_s, NULL) != 0)
        return -1;

    state->bytes = after;
    double start_s, finish_s;
    if (reserve_resource(&context->machines[machine_index], release_s,
                         duration_s, &start_s, &finish_s) != 0)
    {
        context_fail(context, "could not reserve target machine for inverse NFR operation");
        return -1;
    }
    return push_event(context, finish_s, EVENT_EDGE_OPERATION_DONE,
                      instance, -1, edge_index);
}

static int schedule_link_segment(struct simulation_context *context,
                                 int instance,
                                 int edge_index,
                                 double release_s)
{
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];
    int link_index = state->route[state->route_position];
    const struct link_node *link = &context->configuration->links[link_index];
    double duration_s = link->latency_s + state->bytes / link->bandwidth_Bps;
    double start_s, finish_s;

    if (reserve_resource(&context->links[link_index], release_s,
                         duration_s, &start_s, &finish_s) != 0)
    {
        context_fail(context, "could not reserve continuum network link");
        return -1;
    }

    struct link_result *metric = &context->result->links[link_index];
    metric->transfers++;
    metric->bytes += state->bytes;
    metric->busy_slot_seconds += duration_s;
    metric->energy_j += state->bytes * link->energy_per_byte;

    return push_event(context, finish_s, EVENT_LINK_SEGMENT_DONE,
                      instance, -1, edge_index);
}

static int schedule_edge_next(struct simulation_context *context,
                              int instance,
                              int edge_index,
                              double release_s)
{
    const struct config *configuration = context->configuration;
    const struct dag_edge *edge = &configuration->edges[edge_index];
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, instance, edge_index)];

    if (state->phase == EDGE_PHASE_OUTPUT)
    {
        if (state->operation_index < edge->pipeline_count)
            return schedule_output_operation(context, instance, edge_index, release_s);

        int source_machine = configuration->tasks[edge->source_task].machine_index;
        int target_machine = configuration->tasks[edge->target_task].machine_index;
        state->phase = EDGE_PHASE_TRANSFER;
        state->route_position = 0;
        if (find_route(configuration, source_machine, target_machine, state->bytes,
                       state->route, &state->route_length) != 0)
        {
            context_fail(context, "network route disappeared during simulation");
            return -1;
        }
        if (state->route_length > 0)
            return schedule_link_segment(context, instance, edge_index, release_s);

        state->phase = EDGE_PHASE_INPUT;
        state->operation_index = edge->pipeline_count - 1;
    }

    if (state->phase == EDGE_PHASE_TRANSFER)
    {
        if (state->route_position < state->route_length)
            return schedule_link_segment(context, instance, edge_index, release_s);
        state->phase = EDGE_PHASE_INPUT;
        state->operation_index = edge->pipeline_count - 1;
    }

    if (state->phase == EDGE_PHASE_INPUT)
    {
        if (state->operation_index >= 0)
            return schedule_input_operation(context, instance, edge_index, release_s);
        return complete_edge(context, instance, edge_index, release_s);
    }

    return 0;
}

static int handle_task_done(struct simulation_context *context,
                            const struct event *event)
{
    const struct config *configuration = context->configuration;
    const struct dag_task *task = &configuration->tasks[event->task];
    struct task_state *state =
        &context->task_states[task_state_offset(context, event->instance, event->task)];
    struct task_result *metric = &context->result->tasks[event->task];

    if (state->completed)
        return 0;
    state->completed = 1;
    metric->executions++;
    metric->total_duration_s += state->finish_s - state->start_s;
    if (metric->executions == 1 || state->start_s < metric->first_start_s)
        metric->first_start_s = state->start_s;
    if (state->finish_s > metric->last_finish_s)
        metric->last_finish_s = state->finish_s;

    if (task->outdegree == 0)
    {
        context->remaining_sinks[event->instance]--;
        if (context->remaining_sinks[event->instance] == 0)
        {
            context->result->instances_completed++;
            context->result->last_completion_s =
                fmax(context->result->last_completion_s, event->time_s);
        }
    }

    for (int edge_index = 0; edge_index < configuration->edges_number; ++edge_index)
    {
        if (configuration->edges[edge_index].source_task == event->task)
        {
            if (begin_edge(context, event->instance, edge_index,
                           event->time_s, state->output_bytes) != 0)
                return -1;
        }
    }
    return 0;
}

static int handle_edge_operation_done(struct simulation_context *context,
                                      const struct event *event)
{
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, event->instance, event->edge)];
    if (state->phase == EDGE_PHASE_OUTPUT)
        state->operation_index++;
    else if (state->phase == EDGE_PHASE_INPUT)
        state->operation_index--;
    else
    {
        context_fail(context, "edge operation completed in an invalid phase");
        return -1;
    }
    return schedule_edge_next(context, event->instance, event->edge, event->time_s);
}

static int handle_link_done(struct simulation_context *context,
                            const struct event *event)
{
    struct edge_state *state =
        &context->edge_states[edge_state_offset(context, event->instance, event->edge)];
    if (state->phase != EDGE_PHASE_TRANSFER)
    {
        context_fail(context, "link segment completed in an invalid phase");
        return -1;
    }
    state->route_position++;
    return schedule_edge_next(context, event->instance, event->edge, event->time_s);
}

/* ------------------------------------------------------------------------- */
/* Energy integration                                                        */
/* ------------------------------------------------------------------------- */

struct utilization_event {
    double time_s;
    int delta;
};

static int compare_utilization_events(const void *left, const void *right)
{
    const struct utilization_event *a = left;
    const struct utilization_event *b = right;
    if (a->time_s < b->time_s) return -1;
    if (a->time_s > b->time_s) return 1;
    return a->delta - b->delta;
}

static int integrate_machine_energy(const struct machine_node *machine,
                                    const struct resource_runtime *runtime,
                                    double horizon_start,
                                    double horizon_end,
                                    struct machine_result *result)
{
    size_t count = runtime->interval_count * 2;
    struct utilization_event *events = NULL;
    double previous = horizon_start;
    double energy = 0.0;
    double active_seconds = 0.0;
    int active_slots = 0;

    if (count > 0)
    {
        events = malloc(count * sizeof(*events));
        if (!events)
            return -1;
        for (size_t index = 0; index < runtime->interval_count; ++index)
        {
            events[index * 2].time_s = fmax(horizon_start, runtime->intervals[index].start_s);
            events[index * 2].delta = 1;
            events[index * 2 + 1].time_s = fmin(horizon_end, runtime->intervals[index].end_s);
            events[index * 2 + 1].delta = -1;
        }
        qsort(events, count, sizeof(*events), compare_utilization_events);
    }

    size_t index = 0;
    while (index < count)
    {
        double time = events[index].time_s;
        if (time > previous)
        {
            double utilization = (double)active_slots / (double)runtime->slots;
            energy += get_power(machine, utilization) * (time - previous);
            if (active_slots > 0)
                active_seconds += time - previous;
            previous = time;
        }
        while (index < count && fabs(events[index].time_s - time) < 1e-12)
        {
            active_slots += events[index].delta;
            index++;
        }
        if (active_slots < 0)
            active_slots = 0;
        if (active_slots > runtime->slots)
            active_slots = runtime->slots;
    }

    if (horizon_end > previous)
    {
        double utilization = (double)active_slots / (double)runtime->slots;
        energy += get_power(machine, utilization) * (horizon_end - previous);
        if (active_slots > 0)
            active_seconds += horizon_end - previous;
    }

    free(events);
    result->busy_slot_seconds = runtime->busy_slot_seconds;
    result->active_seconds = active_seconds;
    double horizon = horizon_end - horizon_start;
    result->average_utilization = horizon > 0.0 ?
        runtime->busy_slot_seconds / ((double)runtime->slots * horizon) : 0.0;
    if (result->average_utilization > 1.0)
        result->average_utilization = 1.0;
    result->energy_j = energy;
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Public simulation entry point                                             */
/* ------------------------------------------------------------------------- */

static void free_context(struct simulation_context *context)
{
    if (!context)
        return;
    for (int machine = 0; machine < context->configuration->machines_number; ++machine)
        resource_free(&context->machines[machine]);
    for (int link = 0; link < context->configuration->links_number; ++link)
        resource_free(&context->links[link]);
    free(context->task_states);
    free(context->edge_states);
    free(context->remaining_sinks);
    free(context->arrivals);
    free(context->input_sizes);
    free(context->events.items);
}

int run_dag_simulation(const struct config *configuration,
                       struct simulation_result *result,
                       char *error_buffer,
                       size_t error_buffer_size)
{
    struct simulation_context context;
    int sink_count = 0;
    char service_error[512];

    if (!configuration || !result)
    {
        set_error(error_buffer, error_buffer_size, "run_dag_simulation received null input");
        return -1;
    }

    memset(result, 0, sizeof(*result));
    copy_string(result->status, sizeof(result->status), "error");
    result->seed = configuration->seed;
    copy_string(result->config_hash, sizeof(result->config_hash), configuration->config_hash);
    result->instances_expected = configuration->workload.instances;
    result->machines_number = configuration->machines_number;
    result->links_number = configuration->links_number;
    result->tasks_number = configuration->tasks_number;

    memset(&context, 0, sizeof(context));
    context.configuration = configuration;
    context.result = result;
    pcg32_seed(&context.rng, configuration->seed);

    if (service_time_init(configuration, ".", service_error, sizeof(service_error)) != 0)
    {
        set_error(error_buffer, error_buffer_size, service_error);
        return -1;
    }

    if ((size_t)configuration->workload.instances > SIZE_MAX / (size_t)configuration->tasks_number ||
        (configuration->edges_number > 0 &&
         (size_t)configuration->workload.instances > SIZE_MAX / (size_t)configuration->edges_number))
    {
        set_error(error_buffer, error_buffer_size, "simulation state size overflows address space");
        service_time_shutdown();
        return -1;
    }
    size_t task_states_count = (size_t)configuration->workload.instances *
                               (size_t)configuration->tasks_number;
    size_t edge_states_count = (size_t)configuration->workload.instances *
                               (size_t)configuration->edges_number;
    context.task_states = calloc(task_states_count, sizeof(*context.task_states));
    context.edge_states = edge_states_count ?
                          calloc(edge_states_count, sizeof(*context.edge_states)) : NULL;
    context.remaining_sinks = calloc((size_t)configuration->workload.instances,
                                     sizeof(*context.remaining_sinks));
    context.arrivals = calloc((size_t)configuration->workload.instances,
                              sizeof(*context.arrivals));
    context.input_sizes = calloc((size_t)configuration->workload.instances,
                                 sizeof(*context.input_sizes));
    if (!context.task_states || (edge_states_count && !context.edge_states) ||
        !context.remaining_sinks || !context.arrivals || !context.input_sizes)
    {
        set_error(error_buffer, error_buffer_size, "out of memory allocating simulation state");
        free_context(&context);
        service_time_shutdown();
        return -1;
    }

    for (int machine = 0; machine < configuration->machines_number; ++machine)
    {
        if (resource_init(&context.machines[machine], configuration->machines[machine].slots) != 0)
        {
            set_error(error_buffer, error_buffer_size, "out of memory allocating machine resources");
            free_context(&context);
            service_time_shutdown();
            return -1;
        }
        copy_string(result->machines[machine].name,
                    sizeof(result->machines[machine].name),
                    configuration->machines[machine].name);
    }
    for (int link = 0; link < configuration->links_number; ++link)
    {
        if (resource_init(&context.links[link], configuration->links[link].slots) != 0)
        {
            set_error(error_buffer, error_buffer_size, "out of memory allocating link resources");
            free_context(&context);
            service_time_shutdown();
            return -1;
        }
        copy_string(result->links[link].id, sizeof(result->links[link].id),
                    configuration->links[link].id);
        copy_string(result->links[link].from, sizeof(result->links[link].from),
                    configuration->links[link].from);
        copy_string(result->links[link].to, sizeof(result->links[link].to),
                    configuration->links[link].to);
    }
    for (int task = 0; task < configuration->tasks_number; ++task)
    {
        copy_string(result->tasks[task].id, sizeof(result->tasks[task].id),
                    configuration->tasks[task].id);
        result->tasks[task].first_start_s = DBL_MAX;
        if (configuration->tasks[task].outdegree == 0)
            sink_count++;
    }
    if (sink_count == 0)
    {
        set_error(error_buffer, error_buffer_size, "DAG contains no sink task");
        free_context(&context);
        service_time_shutdown();
        return -1;
    }

    double arrival = 0.0;
    for (int instance = 0; instance < configuration->workload.instances; ++instance)
    {
        if (instance > 0)
        {
            if (strcasecmp(configuration->workload.arrival_model, "burst") == 0)
            {
                arrival = 0.0;
            }
            else if (strcasecmp(configuration->workload.arrival_model, "exponential") == 0 ||
                     strcasecmp(configuration->workload.arrival_model, "poisson") == 0)
            {
                double mean = configuration->workload.mean_interarrival_s;
                arrival += mean > 0.0 ? -mean * log(rng_uniform_open(&context.rng)) : 0.0;
            }
            else
            {
                arrival += configuration->workload.mean_interarrival_s;
            }
        }
        context.arrivals[instance] = arrival;
        double size = configuration->workload.input_size_bytes;
        if (configuration->workload.input_size_cv > 0.0)
        {
            size += rng_normal(&context.rng) *
                    configuration->workload.input_size_bytes *
                    configuration->workload.input_size_cv;
            if (size < 1.0)
                size = 1.0;
        }
        context.input_sizes[instance] = size;
        context.remaining_sinks[instance] = sink_count;

        for (int task = 0; task < configuration->tasks_number; ++task)
        {
            struct task_state *state =
                &context.task_states[task_state_offset(&context, instance, task)];
            state->remaining_inputs = configuration->tasks[task].indegree;
            if (state->remaining_inputs == 0)
            {
                state->input_bytes = size * configuration->tasks[task].source_input_size_factor;
                state->ready_time_s = arrival;
                if (push_event(&context, arrival, EVENT_TASK_READY,
                               instance, task, -1) != 0)
                    break;
            }
        }
        if (context.failed)
            break;
    }

    result->first_arrival_s = configuration->workload.instances > 0 ? context.arrivals[0] : 0.0;

    struct event event;
    while (!context.failed && heap_pop(&context.events, &event) == 0)
    {
        switch (event.type)
        {
        case EVENT_TASK_READY:
            if (schedule_task(&context, event.instance, event.task, event.time_s) != 0)
                context.failed = 1;
            break;
        case EVENT_TASK_DONE:
            if (handle_task_done(&context, &event) != 0)
                context.failed = 1;
            break;
        case EVENT_EDGE_OPERATION_DONE:
            if (handle_edge_operation_done(&context, &event) != 0)
                context.failed = 1;
            break;
        case EVENT_LINK_SEGMENT_DONE:
            if (handle_link_done(&context, &event) != 0)
                context.failed = 1;
            break;
        default:
            context_fail(&context, "unknown simulation event");
            break;
        }
    }

    if (!context.failed && result->instances_completed != result->instances_expected)
    {
        char message[256];
        snprintf(message, sizeof(message),
                 "simulation ended with %d/%d workflow instances completed",
                 result->instances_completed, result->instances_expected);
        context_fail(&context, message);
    }

    if (context.failed)
    {
        copy_string(result->error, sizeof(result->error), context.error);
        set_error(error_buffer, error_buffer_size, context.error);
        free_context(&context);
        service_time_shutdown();
        return -1;
    }

    result->makespan_s = result->last_completion_s - result->first_arrival_s;
    if (result->makespan_s < 0.0)
        result->makespan_s = 0.0;

    double machine_energy = 0.0;
    for (int machine = 0; machine < configuration->machines_number; ++machine)
    {
        if (integrate_machine_energy(&configuration->machines[machine],
                                     &context.machines[machine],
                                     result->first_arrival_s,
                                     result->last_completion_s,
                                     &result->machines[machine]) != 0)
        {
            set_error(error_buffer, error_buffer_size, "out of memory integrating machine energy");
            free_context(&context);
            service_time_shutdown();
            return -1;
        }
        machine_energy += result->machines[machine].energy_j;
    }

    result->network_energy_j = 0.0;
    for (int link = 0; link < configuration->links_number; ++link)
        result->network_energy_j += result->links[link].energy_j;
    result->total_energy_j = machine_energy + result->network_energy_j;

    for (int task = 0; task < configuration->tasks_number; ++task)
    {
        struct task_result *metric = &result->tasks[task];
        metric->mean_duration_s = metric->executions > 0 ?
                                  metric->total_duration_s / (double)metric->executions : 0.0;
        if (metric->first_start_s == DBL_MAX)
            metric->first_start_s = 0.0;
    }

    copy_string(result->status, sizeof(result->status), "ok");
    free_context(&context);
    service_time_shutdown();
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Atomic result output                                                      */
/* ------------------------------------------------------------------------- */

static int mkdir_p(const char *path)
{
    char buffer[MAX_PATH_LEN];
    size_t length;

    if (!path || path[0] == '\0')
        return -1;
    copy_string(buffer, sizeof(buffer), path);
    length = strlen(buffer);
    if (length == 0)
        return -1;
    if (buffer[length - 1] == '/')
        buffer[length - 1] = '\0';

    for (char *cursor = buffer + 1; *cursor; ++cursor)
    {
        if (*cursor == '/')
        {
            *cursor = '\0';
            if (mkdir(buffer, 0775) != 0 && errno != EEXIST)
                return -1;
            *cursor = '/';
        }
    }
    if (mkdir(buffer, 0775) != 0 && errno != EEXIST)
        return -1;
    return 0;
}

static int write_csv_files(const struct config *configuration,
                           const struct simulation_result *result,
                           const char *output_dir)
{
    char path[MAX_PATH_LEN];
    FILE *file;

    snprintf(path, sizeof(path), "%s/energy_by_machine.csv", output_dir);
    file = fopen(path, "w");
    if (!file) return -1;
    fprintf(file, "machine_name,power_model,busy_slot_seconds,active_seconds,average_utilization,energy_joules\n");
    for (int machine = 0; machine < result->machines_number; ++machine)
        fprintf(file, "%s,%s,%.9f,%.9f,%.9f,%.9f\n",
                result->machines[machine].name,
                configuration->machines[machine].power_model,
                result->machines[machine].busy_slot_seconds,
                result->machines[machine].active_seconds,
                result->machines[machine].average_utilization,
                result->machines[machine].energy_j);
    fclose(file);

    snprintf(path, sizeof(path), "%s/link_metrics.csv", output_dir);
    file = fopen(path, "w");
    if (!file) return -1;
    fprintf(file, "id,from,to,transfers,bytes,busy_slot_seconds,energy_joules\n");
    for (int link = 0; link < result->links_number; ++link)
        fprintf(file, "%s,%s,%s,%llu,%.3f,%.9f,%.9f\n",
                result->links[link].id,
                result->links[link].from,
                result->links[link].to,
                (unsigned long long)result->links[link].transfers,
                result->links[link].bytes,
                result->links[link].busy_slot_seconds,
                result->links[link].energy_j);
    fclose(file);

    snprintf(path, sizeof(path), "%s/task_metrics.csv", output_dir);
    file = fopen(path, "w");
    if (!file) return -1;
    fprintf(file, "task_id,executions,total_duration_seconds,mean_duration_seconds,first_start_seconds,last_finish_seconds\n");
    for (int task = 0; task < result->tasks_number; ++task)
        fprintf(file, "%s,%llu,%.9f,%.9f,%.9f,%.9f\n",
                result->tasks[task].id,
                (unsigned long long)result->tasks[task].executions,
                result->tasks[task].total_duration_s,
                result->tasks[task].mean_duration_s,
                result->tasks[task].first_start_s,
                result->tasks[task].last_finish_s);
    fclose(file);
    return 0;
}

int write_simulation_results(const struct config *configuration,
                             const struct simulation_result *result,
                             const char *output_dir,
                             char *error_buffer,
                             size_t error_buffer_size)
{
    char final_path[MAX_PATH_LEN];
    char temporary_path[MAX_PATH_LEN];
    json_object *root = NULL;
    json_object *machine_energy = NULL;
    json_object *machines = NULL;
    json_object *links = NULL;
    json_object *tasks = NULL;

    if (!configuration || !result || !output_dir || output_dir[0] == '\0')
    {
        set_error(error_buffer, error_buffer_size, "invalid result output request");
        return -1;
    }
    if (mkdir_p(output_dir) != 0)
    {
        set_error(error_buffer, error_buffer_size, "could not create simulator output directory");
        return -1;
    }

    root = json_object_new_object();
    json_object_object_add(root, "schema_version", json_object_new_int(2));
    json_object_object_add(root, "status", json_object_new_string(result->status));
    json_object_object_add(root, "error", json_object_new_string(result->error));
    json_object_object_add(root, "seed", json_object_new_int64((int64_t)result->seed));
    json_object_object_add(root, "config_hash", json_object_new_string(result->config_hash));
    json_object_object_add(root, "instances_expected", json_object_new_int(result->instances_expected));
    json_object_object_add(root, "instances_completed", json_object_new_int(result->instances_completed));
    json_object_object_add(root, "first_arrival_s", json_object_new_double(result->first_arrival_s));
    json_object_object_add(root, "last_completion_s", json_object_new_double(result->last_completion_s));
    json_object_object_add(root, "makespan_s", json_object_new_double(result->makespan_s));
    json_object_object_add(root, "extrapolated_predictions",
                           json_object_new_int(result->extrapolated_predictions));
    json_object_object_add(root, "missing_predictions",
                           json_object_new_int(result->missing_predictions));

    machine_energy = json_object_new_object();
    machines = json_object_new_array();
    for (int machine = 0; machine < result->machines_number; ++machine)
    {
        const struct machine_result *metric = &result->machines[machine];
        json_object_object_add(machine_energy, metric->name,
                               json_object_new_double(metric->energy_j));
        json_object *entry = json_object_new_object();
        json_object_object_add(entry, "name", json_object_new_string(metric->name));
        json_object_object_add(entry, "busy_slot_seconds",
                               json_object_new_double(metric->busy_slot_seconds));
        json_object_object_add(entry, "active_seconds",
                               json_object_new_double(metric->active_seconds));
        json_object_object_add(entry, "average_utilization",
                               json_object_new_double(metric->average_utilization));
        json_object_object_add(entry, "energy_j", json_object_new_double(metric->energy_j));
        json_object_array_add(machines, entry);
    }
    json_object_object_add(root, "machine_energy_j", machine_energy);
    json_object_object_add(root, "machine_metrics", machines);
    json_object_object_add(root, "network_energy_j",
                           json_object_new_double(result->network_energy_j));
    json_object_object_add(root, "total_energy_j",
                           json_object_new_double(result->total_energy_j));

    links = json_object_new_array();
    for (int link = 0; link < result->links_number; ++link)
    {
        const struct link_result *metric = &result->links[link];
        json_object *entry = json_object_new_object();
        json_object_object_add(entry, "id", json_object_new_string(metric->id));
        json_object_object_add(entry, "from", json_object_new_string(metric->from));
        json_object_object_add(entry, "to", json_object_new_string(metric->to));
        json_object_object_add(entry, "transfers",
                               json_object_new_int64((int64_t)metric->transfers));
        json_object_object_add(entry, "bytes", json_object_new_double(metric->bytes));
        json_object_object_add(entry, "busy_slot_seconds",
                               json_object_new_double(metric->busy_slot_seconds));
        json_object_object_add(entry, "energy_j", json_object_new_double(metric->energy_j));
        json_object_array_add(links, entry);
    }
    json_object_object_add(root, "link_metrics", links);

    tasks = json_object_new_array();
    for (int task = 0; task < result->tasks_number; ++task)
    {
        const struct task_result *metric = &result->tasks[task];
        json_object *entry = json_object_new_object();
        json_object_object_add(entry, "id", json_object_new_string(metric->id));
        json_object_object_add(entry, "executions",
                               json_object_new_int64((int64_t)metric->executions));
        json_object_object_add(entry, "total_duration_s",
                               json_object_new_double(metric->total_duration_s));
        json_object_object_add(entry, "mean_duration_s",
                               json_object_new_double(metric->mean_duration_s));
        json_object_object_add(entry, "first_start_s",
                               json_object_new_double(metric->first_start_s));
        json_object_object_add(entry, "last_finish_s",
                               json_object_new_double(metric->last_finish_s));
        json_object_array_add(tasks, entry);
    }
    json_object_object_add(root, "task_metrics", tasks);

    snprintf(final_path, sizeof(final_path), "%s/run_summary.json", output_dir);
    snprintf(temporary_path, sizeof(temporary_path), "%s/run_summary.json.tmp.%ld",
             output_dir, (long)getpid());
    if (json_object_to_file_ext(temporary_path, root,
                                JSON_C_TO_STRING_PRETTY | JSON_C_TO_STRING_NOSLASHESCAPE) != 0)
    {
        json_object_put(root);
        set_error(error_buffer, error_buffer_size, "could not write temporary run summary");
        return -1;
    }
    json_object_put(root);
    if (rename(temporary_path, final_path) != 0)
    {
        unlink(temporary_path);
        set_error(error_buffer, error_buffer_size, "could not atomically publish run summary");
        return -1;
    }

    if (write_csv_files(configuration, result, output_dir) != 0)
    {
        set_error(error_buffer, error_buffer_size, "could not write diagnostic CSV files");
        return -1;
    }
    return 0;
}
