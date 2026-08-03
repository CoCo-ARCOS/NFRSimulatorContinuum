#define _POSIX_C_SOURCE 200809L

#include "service_time.h"

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

enum calibration_kind {
    CAL_COMPRESS = 0,
    CAL_DECOMPRESS,
    CAL_HASH,
    CAL_ENCRYPT,
    CAL_DECRYPT,
    CAL_ERASURE_ENCODE,
    CAL_ERASURE_DECODE
};

struct calibration_point {
    enum calibration_kind kind;
    char algorithm[32];
    int key_bits;
    int k;
    int m;
    double size_bytes;
    double time_s;
    double stddev_s;
    double ratio;
};

struct service_profile {
    char name[MAX_NAME_LEN];
    char source_dir[MAX_PATH_LEN];
    struct calibration_point *points;
    size_t count;
    size_t capacity;
    int loaded;
};

static struct service_profile profiles[MAX_MACHINES + 1];
static int profile_count = 0;
static int default_profile_index = -1;
static int machine_profile_index[MAX_MACHINES];
static int configured_machine_count = 0;
static int allow_extrapolation = 0;
static int strict_calibration = 1;
static int use_log_log = 0;

static void set_error(char *buffer, size_t size, const char *message)
{
    if (!buffer || size == 0)
        return;
    snprintf(buffer, size, "%s", message ? message : "unknown service-time error");
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
    if (!source)
        return;
    snprintf(destination, destination_size, "%s", source);
}

static char *trim(char *text)
{
    char *end;
    if (!text)
        return text;
    while (*text && isspace((unsigned char)*text))
        ++text;
    if (*text == '\0')
        return text;
    end = text + strlen(text) - 1;
    while (end > text && isspace((unsigned char)*end))
        *end-- = '\0';
    return text;
}

static int parse_double_strict(const char *text, double *value)
{
    char *end = NULL;
    double parsed;

    if (!text || !value)
        return -1;
    errno = 0;
    parsed = strtod(text, &end);
    if (errno != 0 || end == text)
        return -1;
    while (*end && isspace((unsigned char)*end))
        ++end;
    if (*end == 'x' || *end == 'X')
    {
        ++end;
        while (*end && isspace((unsigned char)*end))
            ++end;
    }
    if (*end != '\0')
        return -1;
    if (!isfinite(parsed))
        return -1;
    *value = parsed;
    return 0;
}

static int parse_int_strict(const char *text, int *value)
{
    char *end = NULL;
    long parsed;

    if (!text || !value)
        return -1;
    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text)
        return -1;
    while (*end && isspace((unsigned char)*end))
        ++end;
    if (*end != '\0' || parsed < INT_MIN || parsed > INT_MAX)
        return -1;
    *value = (int)parsed;
    return 0;
}

static int split_csv_line(char *line, char **columns, int max_columns)
{
    int count = 0;
    char *save = NULL;
    char *token;

    if (!line || !columns || max_columns <= 0)
        return 0;

    token = strtok_r(line, ",", &save);
    while (token && count < max_columns)
    {
        columns[count++] = trim(token);
        token = strtok_r(NULL, ",", &save);
    }
    return count;
}

static int ensure_capacity(struct service_profile *profile)
{
    if (profile->count < profile->capacity)
        return 0;

    size_t new_capacity = profile->capacity ? profile->capacity * 2 : 128;
    struct calibration_point *new_points =
        realloc(profile->points, new_capacity * sizeof(*new_points));
    if (!new_points)
        return -1;

    profile->points = new_points;
    profile->capacity = new_capacity;
    return 0;
}

static int append_point(struct service_profile *profile,
                        enum calibration_kind kind,
                        const char *algorithm,
                        int key_bits,
                        int k,
                        int m,
                        double size_mb,
                        double time_s,
                        double stddev_s,
                        double ratio)
{
    struct calibration_point *point;

    if (!profile || !algorithm || algorithm[0] == '\0' ||
        size_mb <= 0.0 || time_s <= 0.0)
        return -1;
    if (ensure_capacity(profile) != 0)
        return -1;

    point = &profile->points[profile->count++];
    memset(point, 0, sizeof(*point));
    point->kind = kind;
    copy_string(point->algorithm, sizeof(point->algorithm), algorithm);
    point->key_bits = key_bits;
    point->k = k;
    point->m = m;
    point->size_bytes = size_mb * 1048576.0;
    point->time_s = time_s;
    point->stddev_s = fmax(0.0, stddev_s);
    point->ratio = ratio > 0.0 ? ratio : 1.0;
    return 0;
}

static int compare_points(const void *left, const void *right)
{
    const struct calibration_point *a = left;
    const struct calibration_point *b = right;
    int comparison;

    if (a->kind != b->kind)
        return (int)a->kind - (int)b->kind;
    comparison = strcasecmp(a->algorithm, b->algorithm);
    if (comparison != 0)
        return comparison;
    if (a->key_bits != b->key_bits)
        return a->key_bits - b->key_bits;
    if (a->k != b->k)
        return a->k - b->k;
    if (a->m != b->m)
        return a->m - b->m;
    if (a->size_bytes < b->size_bytes)
        return -1;
    if (a->size_bytes > b->size_bytes)
        return 1;
    return 0;
}

static void join_path(char *buffer, size_t size, const char *directory, const char *name)
{
    if (!buffer || size == 0)
        return;
    if (!directory || directory[0] == '\0')
        snprintf(buffer, size, "%s", name ? name : "");
    else if (!name || name[0] == '\0')
        snprintf(buffer, size, "%s", directory);
    else
        snprintf(buffer, size, "%s/%s", directory, name);
}

static void resolve_path(char *buffer, size_t size, const char *path, const char *base)
{
    if (!buffer || size == 0)
        return;
    buffer[0] = '\0';
    if (!path || path[0] == '\0')
        return;
    if (path[0] == '/' || !base || base[0] == '\0')
    {
        copy_string(buffer, size, path);
        return;
    }

    size_t base_length = strlen(base);
    size_t path_length = strlen(path);
    if (base_length + 1 + path_length + 1 > size)
        return;
    memcpy(buffer, base, base_length);
    buffer[base_length] = '/';
    memcpy(buffer + base_length + 1, path, path_length + 1);
}

static void load_compression_csv(struct service_profile *profile, const char *path)
{
    FILE *file = fopen(path, "r");
    char line[2048];

    if (!file)
        return;
    (void)fgets(line, sizeof(line), file);

    while (fgets(line, sizeof(line), file))
    {
        char *columns[32];
        int count = split_csv_line(line, columns, 32);
        double size_mb, ratio, comp_s, comp_std = 0.0, decomp_s, decomp_std = 0.0;

        if (count < 5 || parse_double_strict(columns[1], &size_mb) != 0 ||
            parse_double_strict(columns[3], &ratio) != 0 ||
            parse_double_strict(columns[4], &comp_s) != 0)
            continue;
        if (count > 5)
            (void)parse_double_strict(columns[5], &comp_std);
        decomp_s = comp_s;
        if (count > 10)
            (void)parse_double_strict(columns[10], &decomp_s);
        if (count > 11)
            (void)parse_double_strict(columns[11], &decomp_std);

        (void)append_point(profile, CAL_COMPRESS, columns[0], 0, 0, 0,
                           size_mb, comp_s, comp_std, ratio);
        (void)append_point(profile, CAL_DECOMPRESS, columns[0], 0, 0, 0,
                           size_mb, decomp_s, decomp_std, ratio);
    }
    fclose(file);
}

static void load_integrity_csv(struct service_profile *profile, const char *path)
{
    FILE *file = fopen(path, "r");
    char line[2048];

    if (!file)
        return;
    (void)fgets(line, sizeof(line), file);

    while (fgets(line, sizeof(line), file))
    {
        char *columns[16];
        int count = split_csv_line(line, columns, 16);
        double size_mb, time_s, stddev_s = 0.0;

        if (count < 4 || parse_double_strict(columns[1], &size_mb) != 0 ||
            parse_double_strict(columns[3], &time_s) != 0)
            continue;
        if (count > 4)
            (void)parse_double_strict(columns[4], &stddev_s);
        (void)append_point(profile, CAL_HASH, columns[0], 0, 0, 0,
                           size_mb, time_s, stddev_s, 1.0);
    }
    fclose(file);
}

static void load_confidentiality_csv(struct service_profile *profile, const char *path)
{
    FILE *file = fopen(path, "r");
    char line[2048];

    if (!file)
        return;
    (void)fgets(line, sizeof(line), file);

    while (fgets(line, sizeof(line), file))
    {
        char *columns[20];
        int count = split_csv_line(line, columns, 20);
        int key_bits = 0;
        double size_mb, enc_s, enc_std = 0.0, dec_s, dec_std = 0.0;

        if (count < 5 || parse_double_strict(columns[1], &size_mb) != 0 ||
            parse_int_strict(columns[2], &key_bits) != 0 ||
            parse_double_strict(columns[4], &enc_s) != 0)
            continue;
        if (count > 5)
            (void)parse_double_strict(columns[5], &enc_std);
        dec_s = enc_s;
        if (count > 6)
            (void)parse_double_strict(columns[6], &dec_s);
        if (count > 7)
            (void)parse_double_strict(columns[7], &dec_std);

        (void)append_point(profile, CAL_ENCRYPT, columns[0], key_bits, 0, 0,
                           size_mb, enc_s, enc_std, 1.0);
        (void)append_point(profile, CAL_DECRYPT, columns[0], key_bits, 0, 0,
                           size_mb, dec_s, dec_std, 1.0);
    }
    fclose(file);
}

static void load_reliability_csv(struct service_profile *profile, const char *path)
{
    FILE *file = fopen(path, "r");
    char line[2048];

    if (!file)
        return;
    (void)fgets(line, sizeof(line), file);

    while (fgets(line, sizeof(line), file))
    {
        char *columns[20];
        int count = split_csv_line(line, columns, 20);
        int k = 0, m = 0;
        double size_mb, enc_s, enc_std = 0.0, dec_s, dec_std = 0.0;

        if (count < 6 || parse_double_strict(columns[1], &size_mb) != 0 ||
            parse_int_strict(columns[2], &k) != 0 ||
            parse_int_strict(columns[3], &m) != 0 ||
            parse_double_strict(columns[5], &enc_s) != 0 || k <= 0 || m < 0)
            continue;
        if (count > 6)
            (void)parse_double_strict(columns[6], &enc_std);
        dec_s = enc_s;
        if (count > 7)
            (void)parse_double_strict(columns[7], &dec_s);
        if (count > 8)
            (void)parse_double_strict(columns[8], &dec_std);

        (void)append_point(profile, CAL_ERASURE_ENCODE, columns[0], 0, k, m,
                           size_mb, enc_s, enc_std, (double)(k + m) / (double)k);
        (void)append_point(profile, CAL_ERASURE_DECODE, columns[0], 0, k, m,
                           size_mb, dec_s, dec_std, (double)(k + m) / (double)k);
    }
    fclose(file);
}

static int load_profile(struct service_profile *profile,
                        const char *name,
                        const char *directory)
{
    char path[MAX_PATH_LEN];

    if (!profile || !directory || directory[0] == '\0')
        return -1;

    memset(profile, 0, sizeof(*profile));
    copy_string(profile->name, sizeof(profile->name), name ? name : "profile");
    copy_string(profile->source_dir, sizeof(profile->source_dir), directory);

    join_path(path, sizeof(path), directory, "cost-efficiency.csv");
    load_compression_csv(profile, path);
    join_path(path, sizeof(path), directory, "integrity.csv");
    load_integrity_csv(profile, path);
    join_path(path, sizeof(path), directory, "confidentiality.csv");
    load_confidentiality_csv(profile, path);
    join_path(path, sizeof(path), directory, "reliability.csv");
    load_reliability_csv(profile, path);

    if (profile->count == 0)
        return -1;

    qsort(profile->points, profile->count, sizeof(*profile->points), compare_points);
    profile->loaded = 1;
    return 0;
}

void service_time_shutdown(void)
{
    for (int index = 0; index < MAX_MACHINES + 1; ++index)
    {
        free(profiles[index].points);
        memset(&profiles[index], 0, sizeof(profiles[index]));
    }
    profile_count = 0;
    default_profile_index = -1;
    configured_machine_count = 0;
    for (int index = 0; index < MAX_MACHINES; ++index)
        machine_profile_index[index] = -1;
}

int service_time_init(const struct config *configuration,
                      const char *runtime_base_dir,
                      char *error_buffer,
                      size_t error_buffer_size)
{
    char directory[MAX_PATH_LEN];

    if (!configuration)
    {
        set_error(error_buffer, error_buffer_size, "service_time_init received a null configuration");
        return -1;
    }

    service_time_shutdown();
    configured_machine_count = configuration->machines_number;
    allow_extrapolation = configuration->allow_extrapolation;
    strict_calibration = configuration->strict_calibration;
    use_log_log = strcasecmp(configuration->service_time_model, "log-log") == 0 ||
                  strcasecmp(configuration->service_time_model, "log_log") == 0 ||
                  strcasecmp(configuration->service_time_model, "loglog") == 0;

    if (configuration->real_values_dir[0] != '\0')
        resolve_path(directory, sizeof(directory), configuration->real_values_dir, runtime_base_dir);
    else
        join_path(directory, sizeof(directory), runtime_base_dir ? runtime_base_dir : ".", "real_values");

    if (profile_count < MAX_MACHINES + 1 &&
        load_profile(&profiles[profile_count], "default", directory) == 0)
    {
        default_profile_index = profile_count++;
    }

    for (int machine = 0; machine < configuration->machines_number; ++machine)
    {
        const struct machine_node *node = &configuration->machines[machine];
        int requested_specific_profile = 0;
        machine_profile_index[machine] = default_profile_index;
        directory[0] = '\0';

        if (node->real_values_dir[0] != '\0')
        {
            requested_specific_profile = 1;
            resolve_path(directory, sizeof(directory), node->real_values_dir, runtime_base_dir);
        }
        else if (node->hardware_profile[0] != '\0' &&
                 strcasecmp(node->hardware_profile, "default") != 0)
        {
            char root[MAX_PATH_LEN];
            char profile_dir[MAX_PATH_LEN];
            requested_specific_profile = 1;
            join_path(root, sizeof(root), runtime_base_dir ? runtime_base_dir : ".",
                      "results_different_machines/organized");
            join_path(profile_dir, sizeof(profile_dir), root, node->hardware_profile);
            join_path(directory, sizeof(directory), profile_dir, "real_values");
        }

        if (directory[0] != '\0' && profile_count < MAX_MACHINES + 1 &&
            load_profile(&profiles[profile_count], node->name, directory) == 0)
        {
            machine_profile_index[machine] = profile_count++;
        }
        else if (requested_specific_profile && strict_calibration)
        {
            machine_profile_index[machine] = -1;
        }
    }

    /* A missing profile is not an initialization error because every NFR may
       provide an explicit throughput model. Missing predictions still fail
       hard at lookup time; no cross-family fallback is used. */
    return 0;
}

static enum calibration_kind prediction_kind(enum nfr_type type, int inverse)
{
    switch (type)
    {
    case NFR_COMPRESS:
        return inverse ? CAL_DECOMPRESS : CAL_COMPRESS;
    case NFR_ENCRYPT:
        return inverse ? CAL_DECRYPT : CAL_ENCRYPT;
    case NFR_ERASURE:
        return inverse ? CAL_ERASURE_DECODE : CAL_ERASURE_ENCODE;
    case NFR_HASH:
        return CAL_HASH;
    default:
        return CAL_HASH;
    }
}

static int point_matches(const struct calibration_point *point,
                         enum calibration_kind kind,
                         const struct nfr_operation *operation)
{
    if (!point || !operation || point->kind != kind ||
        strcasecmp(point->algorithm, operation->algorithm) != 0)
        return 0;

    if ((kind == CAL_ENCRYPT || kind == CAL_DECRYPT) &&
        operation->key_bits > 0 && point->key_bits > 0 &&
        point->key_bits != operation->key_bits)
        return 0;

    if ((kind == CAL_ERASURE_ENCODE || kind == CAL_ERASURE_DECODE) &&
        (point->k != operation->k || point->m != operation->m))
        return 0;

    return 1;
}

static double interpolate_linear(double x, double x0, double x1, double y0, double y1)
{
    if (fabs(x1 - x0) < 1e-15)
        return y0;
    return y0 + (y1 - y0) * ((x - x0) / (x1 - x0));
}

static double interpolate_log_log(double x, double x0, double x1, double y0, double y1)
{
    if (x <= 0.0 || x0 <= 0.0 || x1 <= 0.0 || y0 <= 0.0 || y1 <= 0.0)
        return interpolate_linear(x, x0, x1, y0, y1);
    if (fabs(log(x1) - log(x0)) < 1e-15)
        return y0;
    double fraction = (log(x) - log(x0)) / (log(x1) - log(x0));
    return exp(log(y0) + (log(y1) - log(y0)) * fraction);
}

static int predict_from_profile(const struct service_profile *profile,
                                enum calibration_kind kind,
                                const struct nfr_operation *operation,
                                double size_bytes,
                                struct service_prediction *prediction,
                                char *error_buffer,
                                size_t error_buffer_size)
{
    const struct calibration_point *lower = NULL;
    const struct calibration_point *upper = NULL;
    const struct calibration_point *first = NULL;
    const struct calibration_point *second = NULL;
    const struct calibration_point *penultimate = NULL;
    const struct calibration_point *last = NULL;
    int matches = 0;

    if (!profile || !profile->loaded)
        return -1;

    for (size_t index = 0; index < profile->count; ++index)
    {
        const struct calibration_point *point = &profile->points[index];
        if (!point_matches(point, kind, operation))
            continue;

        ++matches;
        if (!first)
            first = point;
        else if (!second)
            second = point;
        penultimate = last;
        last = point;

        if (point->size_bytes <= size_bytes)
            lower = point;
        if (!upper && point->size_bytes >= size_bytes)
            upper = point;
    }

    if (matches == 0)
        return -1;

    prediction->extrapolated = 0;
    if (lower && upper && lower == upper)
    {
        prediction->mean_time_s = lower->time_s;
        prediction->stddev_time_s = lower->stddev_s;
        prediction->ratio = lower->ratio;
        return 0;
    }

    if (lower && upper)
    {
        double (*interpolate)(double, double, double, double, double) =
            use_log_log ? interpolate_log_log : interpolate_linear;
        prediction->mean_time_s = interpolate(size_bytes,
                                              lower->size_bytes, upper->size_bytes,
                                              lower->time_s, upper->time_s);
        prediction->stddev_time_s = fmax(0.0,
            interpolate_linear(size_bytes,
                               lower->size_bytes, upper->size_bytes,
                               lower->stddev_s, upper->stddev_s));
        prediction->ratio = fmax(1e-12,
            interpolate_linear(size_bytes,
                               lower->size_bytes, upper->size_bytes,
                               lower->ratio, upper->ratio));
        return 0;
    }

    if (!allow_extrapolation)
    {
        char message[512];
        snprintf(message, sizeof(message),
                 "size %.0f B is outside calibration range for %s in profile %s",
                 size_bytes, operation->algorithm, profile->name);
        set_error(error_buffer, error_buffer_size, message);
        return -2;
    }

    prediction->extrapolated = 1;
    const struct calibration_point *a;
    const struct calibration_point *b;

    if (!lower)
    {
        a = first;
        b = second ? second : first;
    }
    else
    {
        a = penultimate ? penultimate : last;
        b = last;
    }

    if (!a || !b)
        return -1;

    if (a == b)
    {
        prediction->mean_time_s = a->time_s * (size_bytes / a->size_bytes);
        prediction->stddev_time_s = a->stddev_s * (size_bytes / a->size_bytes);
        prediction->ratio = a->ratio;
    }
    else
    {
        double (*interpolate)(double, double, double, double, double) =
            use_log_log ? interpolate_log_log : interpolate_linear;
        prediction->mean_time_s = interpolate(size_bytes,
                                              a->size_bytes, b->size_bytes,
                                              a->time_s, b->time_s);
        prediction->stddev_time_s = fmax(0.0,
            interpolate_linear(size_bytes,
                               a->size_bytes, b->size_bytes,
                               a->stddev_s, b->stddev_s));
        prediction->ratio = fmax(1e-12,
            interpolate_linear(size_bytes,
                               a->size_bytes, b->size_bytes,
                               a->ratio, b->ratio));
    }

    if (!isfinite(prediction->mean_time_s) || prediction->mean_time_s <= 0.0)
    {
        set_error(error_buffer, error_buffer_size,
                  "extrapolation produced a non-positive service time");
        return -2;
    }
    return 0;
}

int service_time_predict(int machine_index,
                         enum nfr_type type,
                         int inverse,
                         const struct nfr_operation *operation,
                         double logical_size_bytes,
                         struct service_prediction *prediction,
                         char *error_buffer,
                         size_t error_buffer_size)
{
    double throughput;
    double fixed;
    int profile_index = default_profile_index;
    enum calibration_kind kind;

    if (!operation || !prediction || logical_size_bytes <= 0.0 ||
        type <= NFR_NONE || type >= NFR_COUNT)
    {
        set_error(error_buffer, error_buffer_size, "invalid service-time prediction request");
        return -1;
    }

    memset(prediction, 0, sizeof(*prediction));
    throughput = inverse ? operation->decode_throughput_Bps : operation->encode_throughput_Bps;
    if (throughput <= 0.0 && inverse)
        throughput = operation->encode_throughput_Bps;
    fixed = inverse ? operation->decode_fixed_overhead_s : operation->fixed_overhead_s;
    if (fixed <= 0.0 && inverse)
        fixed = operation->fixed_overhead_s;

    if (throughput > 0.0)
    {
        prediction->mean_time_s = fmax(0.0, fixed) + logical_size_bytes / throughput;
        prediction->stddev_time_s = 0.0;
        prediction->ratio = operation->ratio > 0.0 ? operation->ratio : 1.0;
        prediction->extrapolated = 0;
        return 0;
    }

    if (machine_index >= 0 && machine_index < configured_machine_count)
        profile_index = machine_profile_index[machine_index];
    if (profile_index < 0 || profile_index >= profile_count)
    {
        set_errorf(error_buffer, error_buffer_size,
                   "no calibration profile is available for algorithm '%s' (%s)",
                   operation->algorithm, nfr_type_name(type));
        return -1;
    }

    kind = prediction_kind(type, inverse);
    int status = predict_from_profile(&profiles[profile_index], kind, operation,
                                      logical_size_bytes, prediction,
                                      error_buffer, error_buffer_size);
    if (status == -1 && !strict_calibration &&
        profile_index != default_profile_index && default_profile_index >= 0)
    {
        status = predict_from_profile(&profiles[default_profile_index], kind, operation,
                                      logical_size_bytes, prediction,
                                      error_buffer, error_buffer_size);
    }

    if (status == -1)
    {
        char message[512];
        snprintf(message, sizeof(message),
                 "missing calibration for operation=%s algorithm=%s machine_index=%d",
                 nfr_type_name(type), operation->algorithm, machine_index);
        set_error(error_buffer, error_buffer_size, message);
    }
    return status;
}
