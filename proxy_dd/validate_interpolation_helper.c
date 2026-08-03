#include "proxy.h"
#include "service_time.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static void usage(const char *program)
{
    fprintf(stderr,
            "Usage: %s <real_values_dir> <operation> <algorithm> <size_bytes> "
            "[--machine-dir DIR] [--key-bits N] [--k N --m N] "
            "[--model linear|log-log] [--allow-extrapolation]\n",
            program);
}

static enum nfr_type parse_type(const char *operation, int *inverse)
{
    *inverse = 0;
    if (strcasecmp(operation, "compress") == 0) return NFR_COMPRESS;
    if (strcasecmp(operation, "decompress") == 0) { *inverse = 1; return NFR_COMPRESS; }
    if (strcasecmp(operation, "encrypt") == 0) return NFR_ENCRYPT;
    if (strcasecmp(operation, "decrypt") == 0) { *inverse = 1; return NFR_ENCRYPT; }
    if (strcasecmp(operation, "encode") == 0 || strcasecmp(operation, "erasure") == 0)
        return NFR_ERASURE;
    if (strcasecmp(operation, "decode") == 0 || strcasecmp(operation, "reconstruct") == 0)
    { *inverse = 1; return NFR_ERASURE; }
    if (strcasecmp(operation, "hash") == 0 || strcasecmp(operation, "verify") == 0)
    { *inverse = strcasecmp(operation, "verify") == 0; return NFR_HASH; }
    return NFR_NONE;
}

static int parse_positive_double(const char *text, double *value)
{
    char *end = NULL;
    errno = 0;
    double parsed = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(parsed) || parsed <= 0.0)
        return -1;
    *value = parsed;
    return 0;
}

static int parse_integer(const char *text, int minimum, int *value)
{
    char *end = NULL;
    long parsed;

    if (!text || !value)
        return -1;
    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' ||
        parsed < minimum || parsed > 2147483647L)
        return -1;
    *value = (int)parsed;
    return 0;
}

static int is_model_name(const char *value)
{
    return value &&
           (strcasecmp(value, "linear") == 0 ||
            strcasecmp(value, "log-log") == 0 ||
            strcasecmp(value, "log_log") == 0 ||
            strcasecmp(value, "loglog") == 0);
}

int main(int argc, char **argv)
{
    struct config configuration;
    struct nfr_operation operation;
    struct service_prediction prediction;
    char error[512] = {0};
    int inverse = 0;
    double size_bytes;

    if (argc < 5)
    {
        usage(argv[0]);
        return 1;
    }

    memset(&configuration, 0, sizeof(configuration));
    memset(&operation, 0, sizeof(operation));
    configuration.schema_version = 2;
    configuration.strict_calibration = 1;
    configuration.machines_number = 1;
    configuration.machines[0].slots = 1;
    configuration.machines[0].speed_factor = 1.0;
    configuration.machines[0].read_bandwidth_Bps = 1.0;
    configuration.machines[0].write_bandwidth_Bps = 1.0;
    snprintf(configuration.machines[0].name, sizeof(configuration.machines[0].name), "validation-machine");
    snprintf(configuration.real_values_dir, sizeof(configuration.real_values_dir), "%s", argv[1]);
    snprintf(configuration.service_time_model, sizeof(configuration.service_time_model), "linear");

    operation.type = parse_type(argv[2], &inverse);
    if (operation.type == NFR_NONE)
    {
        fprintf(stderr, "Unknown operation: %s\n", argv[2]);
        return 2;
    }
    snprintf(operation.algorithm, sizeof(operation.algorithm), "%s", argv[3]);
    operation.key_bits = 256;
    operation.k = 8;
    operation.m = 4;

    if (parse_positive_double(argv[4], &size_bytes) != 0)
    {
        fprintf(stderr, "size_bytes must be a positive finite number\n");
        return 2;
    }

    for (int index = 5; index < argc; ++index)
    {
        if (strcmp(argv[index], "--machine-dir") == 0 && index + 1 < argc)
            snprintf(configuration.machines[0].real_values_dir,
                     sizeof(configuration.machines[0].real_values_dir), "%s", argv[++index]);
        else if (strcmp(argv[index], "--key-bits") == 0 && index + 1 < argc)
        {
            if (parse_integer(argv[++index], 1, &operation.key_bits) != 0)
            {
                fprintf(stderr, "--key-bits requires a positive integer\n");
                return 2;
            }
        }
        else if (strcmp(argv[index], "--k") == 0 && index + 1 < argc)
        {
            if (parse_integer(argv[++index], 1, &operation.k) != 0)
            {
                fprintf(stderr, "--k requires a positive integer\n");
                return 2;
            }
        }
        else if (strcmp(argv[index], "--m") == 0 && index + 1 < argc)
        {
            if (parse_integer(argv[++index], 0, &operation.m) != 0)
            {
                fprintf(stderr, "--m requires a non-negative integer\n");
                return 2;
            }
        }
        else if (strcmp(argv[index], "--model") == 0 && index + 1 < argc)
        {
            const char *model = argv[++index];
            if (!is_model_name(model))
            {
                fprintf(stderr, "--model must be linear or log-log\n");
                return 2;
            }
            snprintf(configuration.service_time_model,
                     sizeof(configuration.service_time_model), "%s", model);
        }
        else if (strcmp(argv[index], "--allow-extrapolation") == 0)
            configuration.allow_extrapolation = 1;
        else
        {
            fprintf(stderr, "Unknown or incomplete option: %s\n", argv[index]);
            usage(argv[0]);
            return 2;
        }
    }

    if (service_time_init(&configuration, ".", error, sizeof(error)) != 0)
    {
        fprintf(stderr, "%s\n", error);
        return 3;
    }
    if (service_time_predict(0, operation.type, inverse, &operation,
                             size_bytes, &prediction, error, sizeof(error)) != 0)
    {
        fprintf(stderr, "%s\n", error);
        service_time_shutdown();
        return 4;
    }

    printf("{\"time_s\":%.12f,\"stddev_s\":%.12f,\"ratio\":%.12f,\"extrapolated\":%s}\n",
           prediction.mean_time_s,
           prediction.stddev_time_s,
           prediction.ratio,
           prediction.extrapolated ? "true" : "false");
    service_time_shutdown();
    return 0;
}
