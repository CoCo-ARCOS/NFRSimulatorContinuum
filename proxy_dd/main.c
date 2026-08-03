#include "proxy.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(const char *program)
{
    fprintf(stderr, "Usage: %s <config.json> [--output-dir DIRECTORY]\n", program);
}

int main(int argc, char **argv)
{
    const char *config_path;
    const char *output_dir = "results";
    char error[1024] = {0};
    struct config *configuration;
    struct simulation_result result;
    int simulation_status;

    if (argc < 2)
    {
        usage(argv[0]);
        return EXIT_FAILURE;
    }
    config_path = argv[1];

    for (int index = 2; index < argc; ++index)
    {
        if (strcmp(argv[index], "--output-dir") == 0 && index + 1 < argc)
        {
            output_dir = argv[++index];
        }
        else
        {
            fprintf(stderr, "Unknown or incomplete argument: %s\n", argv[index]);
            usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    configuration = read_config(config_path, error, sizeof(error));
    if (!configuration)
    {
        fprintf(stderr, "Configuration error: %s\n", error);
        return EXIT_FAILURE;
    }

    memset(&result, 0, sizeof(result));
    simulation_status = run_dag_simulation(configuration, &result, error, sizeof(error));
    if (simulation_status != 0)
    {
        snprintf(result.status, sizeof(result.status), "error");
        if (result.error[0] == '\0')
        {
            size_t length = 0;
            while (length < sizeof(result.error) - 1 && error[length] != '\0')
                ++length;
            memcpy(result.error, error, length);
            result.error[length] = '\0';
        }
    }

    char output_error[1024] = {0};
    if (write_simulation_results(configuration, &result, output_dir,
                                 output_error, sizeof(output_error)) != 0)
    {
        fprintf(stderr, "Result-output error: %s\n", output_error);
        free_config(configuration);
        return EXIT_FAILURE;
    }

    if (simulation_status != 0)
    {
        fprintf(stderr, "Simulation error: %s\n", error);
        free_config(configuration);
        return EXIT_FAILURE;
    }

    printf("DAG simulation completed: instances=%d, makespan=%.6f s, total_energy=%.6f J, network_energy=%.6f J\n",
           result.instances_completed,
           result.makespan_s,
           result.total_energy_j,
           result.network_energy_j);
    printf("Summary: %s/run_summary.json\n", output_dir);

    free_config(configuration);
    return EXIT_SUCCESS;
}
