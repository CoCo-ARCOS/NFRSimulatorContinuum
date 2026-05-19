#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "proxy.h"
#include "service_time.h"

static void usage(const char *prog)
{
    fprintf(stderr, "Usage: %s <operation> <algorithm> <size_bytes> [model]\n", prog);
    fprintf(stderr, "       %s <operation> <algorithm> <size_bytes> [k] [m] [model]\n", prog);
}

static int is_model_name(const char *value)
{
    if (!value)
        return 0;
    return strcasecmp(value, "linear") == 0 ||
           strcasecmp(value, "linear-interpolation") == 0 ||
           strcasecmp(value, "linear_interpolation") == 0 ||
           strcasecmp(value, "log-log") == 0 ||
           strcasecmp(value, "log_log") == 0 ||
           strcasecmp(value, "loglog") == 0 ||
           strcasecmp(value, "power-law") == 0 ||
           strcasecmp(value, "power_law") == 0 ||
           strcasecmp(value, "powerlaw") == 0;
}

int main(int argc, char **argv)
{
    struct config configuration;
    const char *operation;
    const char *algorithm;
    const char *model = "linear";
    double size_bytes;
    double prediction = 0.0;

    if (argc < 4)
    {
        usage(argv[0]);
        return 1;
    }

    memset(&configuration, 0, sizeof(configuration));
    operation = argv[1];
    algorithm = argv[2];
    size_bytes = atof(argv[3]);

    strncpy(configuration.compression_algo, algorithm, sizeof(configuration.compression_algo) - 1);
    configuration.compression_algo[sizeof(configuration.compression_algo) - 1] = '\0';
    strncpy(configuration.hashing_algo, algorithm, sizeof(configuration.hashing_algo) - 1);
    configuration.hashing_algo[sizeof(configuration.hashing_algo) - 1] = '\0';
    strncpy(configuration.ida_algo, algorithm, sizeof(configuration.ida_algo) - 1);
    configuration.ida_algo[sizeof(configuration.ida_algo) - 1] = '\0';

    if (argc >= 5 && is_model_name(argv[4]))
    {
        model = argv[4];
    }
    else if (argc >= 6)
    {
        configuration.ida_k = atoi(argv[4]);
        configuration.ida_m = atoi(argv[5]);
        if (argc >= 7)
            model = argv[6];
    }
    else
    {
        configuration.ida_k = 8;
        configuration.ida_m = 4;
    }
    strncpy(configuration.service_time_model, model, sizeof(configuration.service_time_model) - 1);
    configuration.service_time_model[sizeof(configuration.service_time_model) - 1] = '\0';

    load_service_times(&configuration);

    if (strcmp(operation, "compress") == 0)
        prediction = compressStageAlgo((unsigned long)size_bytes, algorithm);
    else if (strcmp(operation, "decompress") == 0)
        prediction = decompressStageAlgo((unsigned long)size_bytes, algorithm);
    else if (strcmp(operation, "hash") == 0)
        prediction = hashingStageAlgo(size_bytes, algorithm);
    else if (strcmp(operation, "encode") == 0)
        prediction = IDAStageAlgo(size_bytes, algorithm);
    else if (strcmp(operation, "decode") == 0)
        prediction = IDADecodeStageAlgo(size_bytes, algorithm);
    else
    {
        fprintf(stderr, "Unknown operation: %s\n", operation);
        return 2;
    }

    printf("%.12f\n", prediction);
    return 0;
}
