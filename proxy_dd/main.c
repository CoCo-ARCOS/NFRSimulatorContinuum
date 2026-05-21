#include "proxy.h"
#include <libgen.h>

static void requirement_label(const struct nfr_requirement *req, char *buffer, size_t buffer_size)
{
	if (!buffer || buffer_size == 0) {
		return;
	}

	buffer[0] = '\0';
	if (!req || req->type == NFR_NONE) {
		return;
	}

	if (req->algorithm[0] != '\0') {
		snprintf(buffer, buffer_size, "%s:%s", req->task_name, req->algorithm);
		return;
	}

	snprintf(buffer, buffer_size, "%s", req->task_name);
}

static FILE *open_report_csv(const char *file_name)
{
	char path[256];
	mkdir("results", 0777);
	snprintf(path, sizeof(path), "results/%s", file_name);
	FILE *fp = fopen(path, "w");
	if (!fp)
		printf("Warning: could not open %s for writing\n", path);
	return fp;
}

static void write_requirement_headers(FILE *fp, const char *prefix, int count)
{
	if (!fp || !prefix || count <= 0) {
		return;
	}

	for (int i = 0; i < count; ++i) {
		fprintf(fp, ",%s_requirement_%d,%s_requirement_%d_seconds", prefix, i + 1, prefix, i + 1);
	}
}

int main(int argc, char const *argv[]){
	char const 					*filename;
	struct config        		*configuration;
	struct traceConfig		 	*traceData;
	struct worker 			    *arrayWorkers;


	if (argc < 2) {
		fprintf(stderr, "Usage: %s <config_file.json> [service_time_model] [container_platform] [queue_container_image]\n", argv[0]);
		fprintf(stderr, "       %s <config_file.json> [container_platform] [queue_container_image]\n", argv[0]);
		return 1;
	}

	filename = argv[1];


    configuration = read_config(filename);
	if (argc >= 3) {
		if (is_container_platform_name(argv[2])) {
			strncpy(configuration->container_platform, argv[2], sizeof(configuration->container_platform) - 1);
			configuration->container_platform[sizeof(configuration->container_platform) - 1] = '\0';
			if (argc >= 4) {
				strncpy(configuration->queue_container_image, argv[3], sizeof(configuration->queue_container_image) - 1);
				configuration->queue_container_image[sizeof(configuration->queue_container_image) - 1] = '\0';
			}
		} else {
			strncpy(configuration->service_time_model, argv[2], sizeof(configuration->service_time_model) - 1);
			configuration->service_time_model[sizeof(configuration->service_time_model) - 1] = '\0';
			if (argc >= 4) {
				strncpy(configuration->container_platform, argv[3], sizeof(configuration->container_platform) - 1);
				configuration->container_platform[sizeof(configuration->container_platform) - 1] = '\0';
			}
			if (argc >= 5) {
				strncpy(configuration->queue_container_image, argv[4], sizeof(configuration->queue_container_image) - 1);
				configuration->queue_container_image[sizeof(configuration->queue_container_image) - 1] = '\0';
			}
		}
	}
	configure_container_runtime(configuration);

    snprintf(agent_container_prefix, sizeof(agent_container_prefix), "%s_agent", configuration->agent_type);

    // Load dynamic service times from CSV files based on configuration
    {
        char runtime_path[1024];
        char *runtime_dir;

        strncpy(runtime_path, argv[0], sizeof(runtime_path) - 1);
        runtime_path[sizeof(runtime_path) - 1] = '\0';
        runtime_dir = dirname(runtime_path);
        load_service_times_with_base(configuration, runtime_dir ? runtime_dir : ".");
    }

	print_interpolation_points();


	traceData = read_configTrace(configuration->traces_number, configuration->traces_fileName);


	makeContainers( configuration );
	if (!has_inline_traces()) {
		traceGenerator(traceData, configuration->traces_number);
	}
	arrayWorkers = assignation(configuration, traceData);


	// Start execution from the first configured stage; chaining will forward to subsequent stages
	if (configuration->stages_number > 0) {
		deployThread_stages(configuration, arrayWorkers, configuration->stages[0]);
		/* wait for processing to fully complete (timeout 60s) */
		wait_for_outstanding_zero(60);
	}

	/* shutdown NFR managers and print metrics */
	shutdown_and_report_metrics(configuration);

	FILE *stage_totals_csv = open_report_csv("stage_totals_by_workers.csv");
	int max_input_requirements = 0;
	int max_output_requirements = 0;

	for (int si = 0; si < configuration->stages_number; ++si) {
		struct stage_definition *stage_def = &configuration->stage_definitions[si];
		if (stage_def->input_count > max_input_requirements)
			max_input_requirements = stage_def->input_count;
		if (stage_def->output_count > max_output_requirements)
			max_output_requirements = stage_def->output_count;
	}

	printf("\n=== Stage Execution Times ===\n");
	printf("Stage\tName\tWorkers\tObjects\tInputStage(s)\tApplication(s)\tOutputStage(s)\tTotal(s)\n");
	if (stage_totals_csv) {
		fprintf(stage_totals_csv, "stage,stage_name,workers,objects,input_stage_seconds,input_compression_seconds,input_hash_seconds,input_crypto_seconds,application_seconds,output_stage_seconds,output_compression_seconds,output_hash_seconds,output_crypto_seconds,total_seconds");
		write_requirement_headers(stage_totals_csv, "input", max_input_requirements);
		write_requirement_headers(stage_totals_csv, "output", max_output_requirements);
		fprintf(stage_totals_csv, "\n");
	}

	for (int si = 0; si < configuration->stages_number; ++si) {
		int stage_num = configuration->stages[si];
		int stage_idx = stage_num - 1;
		if (stage_idx < 0 || stage_idx >= MAX_STAGES)
			continue;

		struct stage_definition *stage_def = &configuration->stage_definitions[si];
		double input_requirement_times[MAX_PIPELINE_TASKS] = {0.0};
		double output_requirement_times[MAX_PIPELINE_TASKS] = {0.0};
		double max_input_time = 0.0;
		double max_output_time = 0.0;
		double max_application_time = 0.0;
		double max_compression_input = 0.0;
		double max_hash_input = 0.0;
		double max_crypto_input = 0.0;
		double max_compression_output = 0.0;
		double max_hash_output = 0.0;
		double max_crypto_output = 0.0;
		long stage_objects = 0;

		for (int w = 0; w < configuration->workers; ++w) {
			stage_objects += arrayWorkers[w].sizeWorker;
			max_input_time = fmax(max_input_time, arrayWorkers[w].stage_input_time[stage_idx]);
			max_output_time = fmax(max_output_time, arrayWorkers[w].stage_output_time[stage_idx]);
			max_application_time = fmax(max_application_time, arrayWorkers[w].stage_application_time[stage_idx]);
			max_compression_input = fmax(max_compression_input, arrayWorkers[w].stage_nfr_input_time[stage_idx][NFR_COMPRESS]);
			max_compression_output = fmax(max_compression_output, arrayWorkers[w].stage_nfr_output_time[stage_idx][NFR_COMPRESS]);
			max_hash_input = fmax(max_hash_input, arrayWorkers[w].stage_nfr_input_time[stage_idx][NFR_HASH]);
			max_hash_output = fmax(max_hash_output, arrayWorkers[w].stage_nfr_output_time[stage_idx][NFR_HASH]);
			max_crypto_input = fmax(max_crypto_input, arrayWorkers[w].stage_nfr_input_time[stage_idx][NFR_ENCRYPT]);
			max_crypto_output = fmax(max_crypto_output, arrayWorkers[w].stage_nfr_output_time[stage_idx][NFR_ENCRYPT]);

			for (int task = 0; task < stage_def->input_count; ++task) {
				input_requirement_times[task] = fmax(
					input_requirement_times[task],
					arrayWorkers[w].stage_input_requirement_time[stage_idx][task]
				);
			}

			for (int task = 0; task < stage_def->output_count; ++task) {
				output_requirement_times[task] = fmax(
					output_requirement_times[task],
					arrayWorkers[w].stage_output_requirement_time[stage_idx][task]
				);
			}
		}

		double stage_total = max_input_time + max_application_time + max_output_time;

		printf("%d\t%s\t%d\t%ld\t%f\t%f\t%f\t%f\n",
			stage_num,
			stage_def->name,
			configuration->workers,
			stage_objects,
			max_input_time,
			max_application_time,
			max_output_time,
			stage_total);
		if (stage_totals_csv) {
			fprintf(stage_totals_csv, "%d,%s,%d,%ld,%f,%f,%f,%f,%f,%f,%f,%f,%f,%f",
				stage_num,
				stage_def->name,
				configuration->workers,
				stage_objects,
				max_input_time,
				max_compression_input,
				max_hash_input,
				max_crypto_input,
				max_application_time,
				max_output_time,
				max_compression_output,
				max_hash_output,
				max_crypto_output,
				stage_total);

			for (int task = 0; task < max_input_requirements; ++task) {
				char label[96];
				double seconds = 0.0;

				label[0] = '\0';
				if (task < stage_def->input_count) {
					requirement_label(&stage_def->input_requirements[task], label, sizeof(label));
					seconds = input_requirement_times[task];
				}
				fprintf(stage_totals_csv, ",%s,%f", label, seconds);
			}

			for (int task = 0; task < max_output_requirements; ++task) {
				char label[96];
				double seconds = 0.0;

				label[0] = '\0';
				if (task < stage_def->output_count) {
					requirement_label(&stage_def->output_requirements[task], label, sizeof(label));
					seconds = output_requirement_times[task];
				}
				fprintf(stage_totals_csv, ",%s,%f", label, seconds);
			}

			fprintf(stage_totals_csv, "\n");
		}
	}

	if (stage_totals_csv) fclose(stage_totals_csv);

	for (int i = 0; i < configuration->workers; ++i) {
		free(arrayWorkers[i].trace);
	}
	free(arrayWorkers);
	free(traceData);
	free(configuration->traces_fileName);
	free(configuration);

	return 0;
}
