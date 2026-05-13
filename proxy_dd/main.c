#include "proxy.h"

static const char *nfr_type_name(int type)
{
	switch (type) {
		case NFR_COMPRESS:
			return "compression";
		case NFR_ENCRYPT:
			return "cipher";
		case NFR_HASH:
			return "hash";
		default:
			return "unknown";
	}
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

static double avg_object_size(long total_size, long objects)
{
	if (objects <= 0)
		return 0.0;

	return (double)total_size / (double)objects;
}

static int requirements_include_nfr(const struct nfr_requirement *requirements, int count, int type)
{
	if (!requirements || count <= 0)
		return 0;

	for (int i = 0; i < count; ++i) {
		if (requirements[i].type == type)
			return 1;
	}

	return 0;
}

int main(int argc, char const *argv[]){
	int 						mean_sample, opPerWorker, opPerWorkerResidue;
	double 						service_time;
	char const 					*filename;
	struct config        		*configuration;
	struct traceConfig		 	*traceData;
	struct worker 			    *arrayWorkers;
	float 						sth, sti, stc;


	if (argc < 2) {
		fprintf(stderr, "Usage: %s <config_file.json>\n", argv[0]);
		return 1;
	}

	filename = argv[1];


    configuration = read_config(filename);

    snprintf(agent_container_prefix, sizeof(agent_container_prefix), "%s_agent", configuration->agent_type);

    // Load dynamic service times from CSV files based on configuration
    load_service_times(configuration);

	print_interpolation_points();


    traceData = read_configTrace(configuration->traces_number, configuration->traces_fileName);


	opPerWorker = 0;
	opPerWorkerResidue = 0;


	makeContainers( configuration );
	traceGenerator ( traceData , configuration->traces_number ) ;
	arrayWorkers = assignation(configuration, traceData);


	// Start execution from the first configured stage; chaining will forward to subsequent stages
	if (configuration->stages_number > 0) {
		deployThread_stages(configuration, arrayWorkers, configuration->stages[0]);
		/* wait for processing to fully complete (timeout 60s) */
		wait_for_outstanding_zero(60);
	}

	/* shutdown NFR managers and print metrics */
	shutdown_and_report_metrics(configuration);

	FILE *worker_stage_csv = open_report_csv("worker_stage_times.csv");
	FILE *worker_stage_nfr_csv = open_report_csv("worker_stage_nfr_times.csv");
	FILE *stage_nfr_totals_csv = open_report_csv("stage_nfr_totals_by_workers.csv");
	FILE *stage_totals_csv = open_report_csv("stage_totals_by_workers.csv");
	FILE *worker_totals_csv = open_report_csv("worker_totals.csv");
	FILE *pipeline_metrics_csv = open_report_csv("pipeline_metrics.csv");
	FILE *worker_workload_csv = open_report_csv("worker_workload.csv");
	FILE *worker_input_workload_csv = open_report_csv("worker_input_workload.csv");
	FILE *stage_requirements_csv = open_report_csv("stage_requirements.csv");

	printf("\n=== Stage Requirements ===\n");
	printf("Stage\tName\tBFS(MB/s)\tRequirement\tTask\tNFR\tTaskName\tAlgorithm\n");
	if (stage_requirements_csv)
		fprintf(stage_requirements_csv, "stage,stage_name,b_fs_mb_s,requirement_pipeline,task_index,nfr,task_name,algorithm\n");
	for (int si = 0; si < configuration->stages_number; ++si) {
		struct stage_definition *stage_def = &configuration->stage_definitions[si];
		double stage_b_fs_mb = stage_def->b_fs / 1048576.0;
		for (int task = 0; task < stage_def->input_count; ++task) {
			struct nfr_requirement *req = &stage_def->input_requirements[task];
			printf("%d\t%s\t%f\tinput\t%d\t%s\t%s\t%s\n",
				stage_def->stage,
				stage_def->name,
				stage_b_fs_mb,
				task,
				nfr_type_name(req->type),
				req->task_name,
				req->algorithm);
			if (stage_requirements_csv)
				fprintf(stage_requirements_csv, "%d,%s,%f,input,%d,%s,%s,%s\n",
					stage_def->stage,
					stage_def->name,
					stage_b_fs_mb,
					task,
					nfr_type_name(req->type),
					req->task_name,
					req->algorithm);
		}
		for (int task = 0; task < stage_def->output_count; ++task) {
			struct nfr_requirement *req = &stage_def->output_requirements[task];
			printf("%d\t%s\t%f\toutput\t%d\t%s\t%s\t%s\n",
				stage_def->stage,
				stage_def->name,
				stage_b_fs_mb,
				task,
				nfr_type_name(req->type),
				req->task_name,
				req->algorithm);
			if (stage_requirements_csv)
				fprintf(stage_requirements_csv, "%d,%s,%f,output,%d,%s,%s,%s\n",
					stage_def->stage,
					stage_def->name,
					stage_b_fs_mb,
					task,
					nfr_type_name(req->type),
					req->task_name,
					req->algorithm);
		}
	}

	printf("\n=== Worker Workload Size ===\n");
	printf("Worker\tObjects\tTotalInput(bytes)\tAvgInputObject(bytes)\tTotalOutput(bytes)\tAvgOutputObject(bytes)\n");
	if (worker_workload_csv)
		fprintf(worker_workload_csv, "worker,objects,total_input_bytes,avg_input_object_bytes,total_output_bytes,avg_output_object_bytes\n");
	if (worker_input_workload_csv)
		fprintf(worker_input_workload_csv, "worker,objects,total_input_bytes,avg_input_object_bytes,total_output_bytes,avg_output_object_bytes\n");
	for (int w = 0; w < configuration->workers; ++w) {
		double avg_input_size = avg_object_size(arrayWorkers[w].input_workload_size, arrayWorkers[w].sizeWorker);
		double avg_output_size = avg_object_size(arrayWorkers[w].output_workload_size, arrayWorkers[w].sizeWorker);
		printf("%d\t%d\t%ld\t%f\t%ld\t%f\n",
			w,
			arrayWorkers[w].sizeWorker,
			arrayWorkers[w].input_workload_size,
			avg_input_size,
			arrayWorkers[w].output_workload_size,
			avg_output_size);
		if (worker_workload_csv)
			fprintf(worker_workload_csv, "%d,%d,%ld,%f,%ld,%f\n",
				w,
				arrayWorkers[w].sizeWorker,
				arrayWorkers[w].input_workload_size,
				avg_input_size,
				arrayWorkers[w].output_workload_size,
				avg_output_size);
		if (worker_input_workload_csv)
			fprintf(worker_input_workload_csv, "%d,%d,%ld,%f,%ld,%f\n",
				w,
				arrayWorkers[w].sizeWorker,
				arrayWorkers[w].input_workload_size,
				avg_input_size,
				arrayWorkers[w].output_workload_size,
				avg_output_size);
	}

	printf("\n=== Worker Stage Times ===\n");
	printf("Worker\tStage\tName\tObjects\tStageInput(bytes)\tAvgStageInput(bytes)\tStageOutput(bytes)\tAvgStageOutput(bytes)\tInput(s)\tOutput(s)\tTransfer(s)\tTotal(s)\n");
	if (worker_stage_csv)
		fprintf(worker_stage_csv, "worker,stage,stage_name,objects,stage_input_bytes,avg_stage_input_object_bytes,stage_output_bytes,avg_stage_output_object_bytes,input_seconds,output_seconds,transfer_seconds,total_seconds\n");
	for (int w = 0; w < configuration->workers; ++w) {
		for (int si = 0; si < configuration->stages_number; ++si) {
			int stage_num = configuration->stages[si];
			int stage_idx = stage_num - 1;
			if (stage_idx < 0 || stage_idx >= MAX_STAGES)
				continue;

			long stage_input_size = arrayWorkers[w].stage_input_size[stage_idx];
			long stage_output_size = arrayWorkers[w].stage_output_size[stage_idx];
			double avg_stage_input_size = avg_object_size(stage_input_size, arrayWorkers[w].sizeWorker);
			double avg_stage_output_size = avg_object_size(stage_output_size, arrayWorkers[w].sizeWorker);
			double input_time = arrayWorkers[w].stage_input_time[stage_idx];
			double output_time = arrayWorkers[w].stage_output_time[stage_idx];
			double transfer_time = arrayWorkers[w].stage_transfer_time[stage_idx];
			double total_time = input_time + output_time + transfer_time;

			printf("%d\t%d\t%s\t%d\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\t%f\n",
				w,
				stage_num,
				configuration->stage_definitions[si].name,
				arrayWorkers[w].sizeWorker,
				stage_input_size,
				avg_stage_input_size,
				stage_output_size,
				avg_stage_output_size,
				input_time,
				output_time,
				transfer_time,
				total_time);
			if (worker_stage_csv)
				fprintf(worker_stage_csv, "%d,%d,%s,%d,%ld,%f,%ld,%f,%f,%f,%f,%f\n",
					w,
					stage_num,
					configuration->stage_definitions[si].name,
					arrayWorkers[w].sizeWorker,
					stage_input_size,
					avg_stage_input_size,
					stage_output_size,
					avg_stage_output_size,
					input_time,
					output_time,
					transfer_time,
					total_time);
		}
	}

	printf("\n=== Worker Stage NFR Times ===\n");
	printf("Worker\tStage\tName\tRequirement\tTask\tNFR\tTaskName\tAlgorithm\tObjects\tRequirementInput(bytes)\tAvgRequirementInput(bytes)\tRequirementOutput(bytes)\tAvgRequirementOutput(bytes)\tStageInput(bytes)\tAvgStageInput(bytes)\tStageOutput(bytes)\tAvgStageOutput(bytes)\tInput(s)\tOutput(s)\tTotal(s)\n");
	if (worker_stage_nfr_csv)
		fprintf(worker_stage_nfr_csv, "worker,stage,stage_name,requirement_pipeline,task_index,nfr,task_name,algorithm,objects,requirement_input_bytes,avg_requirement_input_object_bytes,requirement_output_bytes,avg_requirement_output_object_bytes,stage_input_bytes,avg_stage_input_object_bytes,stage_output_bytes,avg_stage_output_object_bytes,input_seconds,output_seconds,total_seconds\n");
	for (int w = 0; w < configuration->workers; ++w) {
		for (int si = 0; si < configuration->stages_number; ++si) {
			int stage_num = configuration->stages[si];
			int stage_idx = stage_num - 1;
			if (stage_idx < 0 || stage_idx >= MAX_STAGES)
				continue;

			struct stage_definition *stage_def = &configuration->stage_definitions[si];
			long stage_input_size = arrayWorkers[w].stage_input_size[stage_idx];
			long stage_output_size = arrayWorkers[w].stage_output_size[stage_idx];
			double avg_stage_input_size = avg_object_size(stage_input_size, arrayWorkers[w].sizeWorker);
			double avg_stage_output_size = avg_object_size(stage_output_size, arrayWorkers[w].sizeWorker);

			for (int task = 0; task < stage_def->input_count; ++task) {
				struct nfr_requirement *req = &stage_def->input_requirements[task];
				long req_input_size = arrayWorkers[w].stage_input_requirement_input_size[stage_idx][task];
				long req_output_size = arrayWorkers[w].stage_input_requirement_output_size[stage_idx][task];
				double avg_req_input_size = avg_object_size(req_input_size, arrayWorkers[w].sizeWorker);
				double avg_req_output_size = avg_object_size(req_output_size, arrayWorkers[w].sizeWorker);
				double input_time = arrayWorkers[w].stage_input_requirement_time[stage_idx][task];

				printf("%d\t%d\t%s\tinput\t%d\t%s\t%s\t%s\t%d\t%ld\t%f\t%ld\t%f\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\n",
					w,
					stage_num,
					stage_def->name,
					task,
					nfr_type_name(req->type),
					req->task_name,
					req->algorithm,
					arrayWorkers[w].sizeWorker,
					req_input_size,
					avg_req_input_size,
					req_output_size,
					avg_req_output_size,
					stage_input_size,
					avg_stage_input_size,
					stage_output_size,
					avg_stage_output_size,
					input_time,
					0.0,
					input_time);
				if (worker_stage_nfr_csv)
					fprintf(worker_stage_nfr_csv, "%d,%d,%s,input,%d,%s,%s,%s,%d,%ld,%f,%ld,%f,%ld,%f,%ld,%f,%f,%f,%f\n",
						w,
						stage_num,
						stage_def->name,
						task,
						nfr_type_name(req->type),
						req->task_name,
						req->algorithm,
						arrayWorkers[w].sizeWorker,
						req_input_size,
						avg_req_input_size,
						req_output_size,
						avg_req_output_size,
						stage_input_size,
						avg_stage_input_size,
						stage_output_size,
						avg_stage_output_size,
						input_time,
						0.0,
						input_time);
			}

			for (int task = 0; task < stage_def->output_count; ++task) {
				struct nfr_requirement *req = &stage_def->output_requirements[task];
				long req_input_size = arrayWorkers[w].stage_output_requirement_input_size[stage_idx][task];
				long req_output_size = arrayWorkers[w].stage_output_requirement_output_size[stage_idx][task];
				double avg_req_input_size = avg_object_size(req_input_size, arrayWorkers[w].sizeWorker);
				double avg_req_output_size = avg_object_size(req_output_size, arrayWorkers[w].sizeWorker);
				double output_time = arrayWorkers[w].stage_output_requirement_time[stage_idx][task];

				printf("%d\t%d\t%s\toutput\t%d\t%s\t%s\t%s\t%d\t%ld\t%f\t%ld\t%f\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\n",
					w,
					stage_num,
					stage_def->name,
					task,
					nfr_type_name(req->type),
					req->task_name,
					req->algorithm,
					arrayWorkers[w].sizeWorker,
					req_input_size,
					avg_req_input_size,
					req_output_size,
					avg_req_output_size,
					stage_input_size,
					avg_stage_input_size,
					stage_output_size,
					avg_stage_output_size,
					0.0,
					output_time,
					output_time);
				if (worker_stage_nfr_csv)
					fprintf(worker_stage_nfr_csv, "%d,%d,%s,output,%d,%s,%s,%s,%d,%ld,%f,%ld,%f,%ld,%f,%ld,%f,%f,%f,%f\n",
						w,
						stage_num,
						stage_def->name,
						task,
						nfr_type_name(req->type),
						req->task_name,
						req->algorithm,
						arrayWorkers[w].sizeWorker,
						req_input_size,
						avg_req_input_size,
						req_output_size,
						avg_req_output_size,
						stage_input_size,
						avg_stage_input_size,
						stage_output_size,
						avg_stage_output_size,
						0.0,
						output_time,
						output_time);
			}
		}
	}

	printf("\n=== Stage NFR Totals By Workers ===\n");
	printf("Stage\tName\tRequirement\tNFR\tWorkers\tObjects\tStageInput(bytes)\tAvgStageInput(bytes)\tStageOutput(bytes)\tAvgStageOutput(bytes)\tInput(s)\tOutput(s)\tTotal(s)\tAvgWorker(s)\n");
	if (stage_nfr_totals_csv)
		fprintf(stage_nfr_totals_csv, "stage,stage_name,requirement_pipeline,nfr,workers,objects,stage_input_bytes,avg_stage_input_object_bytes,stage_output_bytes,avg_stage_output_object_bytes,input_seconds,output_seconds,total_seconds,avg_worker_seconds\n");
	for (int si = 0; si < configuration->stages_number; ++si) {
		int stage_num = configuration->stages[si];
		int stage_idx = stage_num - 1;
		if (stage_idx < 0 || stage_idx >= MAX_STAGES)
			continue;

		struct stage_definition *stage_def = &configuration->stage_definitions[si];
		long stage_objects = 0;
		long stage_input_size = 0;
		long stage_output_size = 0;
		for (int w = 0; w < configuration->workers; ++w) {
			stage_objects += arrayWorkers[w].sizeWorker;
			stage_input_size += arrayWorkers[w].stage_input_size[stage_idx];
			stage_output_size += arrayWorkers[w].stage_output_size[stage_idx];
		}
		double stage_avg_input_size = avg_object_size(stage_input_size, stage_objects);
		double stage_avg_output_size = avg_object_size(stage_output_size, stage_objects);

		for (int nf = NFR_COMPRESS; nf < NFR_COUNT; ++nf) {
			double input_time = 0.0;
			double output_time = 0.0;
			for (int w = 0; w < configuration->workers; ++w) {
				input_time += arrayWorkers[w].stage_nfr_input_time[stage_idx][nf];
				output_time += arrayWorkers[w].stage_nfr_output_time[stage_idx][nf];
			}

			int has_input_req = requirements_include_nfr(stage_def->input_requirements, stage_def->input_count, nf);
			int has_output_req = requirements_include_nfr(stage_def->output_requirements, stage_def->output_count, nf);

			if (has_input_req) {
				double avg_worker = configuration->workers > 0 ? input_time / configuration->workers : 0.0;
				printf("%d\t%s\tinput\t%s\t%d\t%ld\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\t%f\n",
					stage_num,
					stage_def->name,
					nfr_type_name(nf),
					configuration->workers,
					stage_objects,
					stage_input_size,
					stage_avg_input_size,
					stage_output_size,
					stage_avg_output_size,
					input_time,
					0.0,
					input_time,
					avg_worker);
				if (stage_nfr_totals_csv)
					fprintf(stage_nfr_totals_csv, "%d,%s,input,%s,%d,%ld,%ld,%f,%ld,%f,%f,%f,%f,%f\n",
						stage_num,
						stage_def->name,
						nfr_type_name(nf),
						configuration->workers,
						stage_objects,
						stage_input_size,
						stage_avg_input_size,
						stage_output_size,
						stage_avg_output_size,
						input_time,
						0.0,
						input_time,
						avg_worker);
			}

			if (has_output_req) {
				double avg_worker = configuration->workers > 0 ? output_time / configuration->workers : 0.0;
				printf("%d\t%s\toutput\t%s\t%d\t%ld\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\t%f\n",
					stage_num,
					stage_def->name,
					nfr_type_name(nf),
					configuration->workers,
					stage_objects,
					stage_input_size,
					stage_avg_input_size,
					stage_output_size,
					stage_avg_output_size,
					0.0,
					output_time,
					output_time,
					avg_worker);
				if (stage_nfr_totals_csv)
					fprintf(stage_nfr_totals_csv, "%d,%s,output,%s,%d,%ld,%ld,%f,%ld,%f,%f,%f,%f,%f\n",
						stage_num,
						stage_def->name,
						nfr_type_name(nf),
						configuration->workers,
						stage_objects,
						stage_input_size,
						stage_avg_input_size,
						stage_output_size,
						stage_avg_output_size,
						0.0,
						output_time,
						output_time,
						avg_worker);
			}
		}
	}

	printf("\n=== Stage Totals By Workers ===\n");
	printf("Stage\tName\tWorkers\tObjects\tStageInput(bytes)\tAvgStageInput(bytes)\tStageOutput(bytes)\tAvgStageOutput(bytes)\tInput(s)\tOutput(s)\tTransfer(s)\tTotal(s)\tAvgWorker(s)\n");
	if (stage_totals_csv)
		fprintf(stage_totals_csv, "stage,stage_name,workers,objects,stage_input_bytes,avg_stage_input_object_bytes,stage_output_bytes,avg_stage_output_object_bytes,input_seconds,output_seconds,transfer_seconds,total_seconds,avg_worker_seconds\n");
	for (int si = 0; si < configuration->stages_number; ++si) {
		int stage_num = configuration->stages[si];
		int stage_idx = stage_num - 1;
		double input_time = 0.0;
		double output_time = 0.0;
		double transfer_time = 0.0;
		long stage_objects = 0;
		long stage_input_size = 0;
		long stage_output_size = 0;
		if (stage_idx < 0 || stage_idx >= MAX_STAGES)
			continue;

		for (int w = 0; w < configuration->workers; ++w) {
			stage_objects += arrayWorkers[w].sizeWorker;
			stage_input_size += arrayWorkers[w].stage_input_size[stage_idx];
			stage_output_size += arrayWorkers[w].stage_output_size[stage_idx];
			input_time += arrayWorkers[w].stage_input_time[stage_idx];
			output_time += arrayWorkers[w].stage_output_time[stage_idx];
			transfer_time += arrayWorkers[w].stage_transfer_time[stage_idx];
		}

		double total_time = input_time + output_time + transfer_time;
		double avg_worker = configuration->workers > 0 ? total_time / configuration->workers : 0.0;
		double stage_avg_input_size = avg_object_size(stage_input_size, stage_objects);
		double stage_avg_output_size = avg_object_size(stage_output_size, stage_objects);
		printf("%d\t%s\t%d\t%ld\t%ld\t%f\t%ld\t%f\t%f\t%f\t%f\t%f\t%f\n",
			stage_num,
			configuration->stage_definitions[si].name,
			configuration->workers,
			stage_objects,
			stage_input_size,
			stage_avg_input_size,
			stage_output_size,
			stage_avg_output_size,
			input_time,
			output_time,
			transfer_time,
			total_time,
			avg_worker);
		if (stage_totals_csv)
			fprintf(stage_totals_csv, "%d,%s,%d,%ld,%ld,%f,%ld,%f,%f,%f,%f,%f,%f\n",
				stage_num,
				configuration->stage_definitions[si].name,
				configuration->workers,
				stage_objects,
				stage_input_size,
				stage_avg_input_size,
				stage_output_size,
				stage_avg_output_size,
				input_time,
				output_time,
				transfer_time,
				total_time,
				avg_worker);
	}

	printf("\n=== Worker Totals ===\n");
	if (worker_totals_csv)
		fprintf(worker_totals_csv, "worker,objects,total_input_bytes,avg_input_object_bytes,total_output_bytes,avg_output_object_bytes,compression_decompression_seconds,hashing_seconds,indexing_seconds,crypto_seconds\n");
	for (int i = 0; i < configuration->workers ; ++i) {
		stc = 0;
		sth = 0;
		sti = 0;
		double avg_input_size = avg_object_size(arrayWorkers[i].input_workload_size, arrayWorkers[i].sizeWorker);
		double avg_output_size = avg_object_size(arrayWorkers[i].output_workload_size, arrayWorkers[i].sizeWorker);
		for (int j = 0; j < arrayWorkers[i].sizeWorker; ++j) {
			stc += arrayWorkers[i].trace[j].service_time_c;
			sth += arrayWorkers[i].trace[j].service_time_h;
			sti += arrayWorkers[i].trace[j].service_time_ida;
		}

		printf("Worker: %d Objects: %d TotalInput(bytes): %ld AvgInputObject(bytes): %f TotalOutput(bytes): %ld AvgOutputObject(bytes): %f Compression/Decompression: %f Hashing: %f Indexing: %f Crypto: %f \n",
			  i, arrayWorkers[i].sizeWorker, arrayWorkers[i].input_workload_size, avg_input_size, arrayWorkers[i].output_workload_size, avg_output_size, stc, sth, arrayWorkers[i].service_time,sti);
		if (worker_totals_csv)
			fprintf(worker_totals_csv, "%d,%d,%ld,%f,%ld,%f,%f,%f,%f,%f\n",
				i,
				arrayWorkers[i].sizeWorker,
				arrayWorkers[i].input_workload_size,
				avg_input_size,
				arrayWorkers[i].output_workload_size,
				avg_output_size,
				stc,
				sth,
				arrayWorkers[i].service_time,
				sti);
	}

	/* Aggregate cumulative task metrics across the full stage chain. Per-stage/task
	   manager metrics are printed above by shutdown_and_report_metrics(). */
	printf("\n=== Pipeline Metrics ===\n");
	double total_compression = 0.0;
	double total_hashing = 0.0;
	double total_crypto = 0.0;
	double total_io = 0.0;
	long processed_objects = 0;
	long total_input_size = 0;
	long total_output_size = 0;
	for (int w = 0; w < configuration->workers; ++w) {
		total_input_size += arrayWorkers[w].input_workload_size;
		total_output_size += arrayWorkers[w].output_workload_size;
		for (int j = 0; j < arrayWorkers[w].sizeWorker; ++j) {
			total_compression += arrayWorkers[w].trace[j].service_time_c;
			total_hashing += arrayWorkers[w].trace[j].service_time_h;
			total_crypto += arrayWorkers[w].trace[j].service_time_ida;
			total_io += arrayWorkers[w].trace[j].service_time_io;
			processed_objects++;
		}
	}

	long configured_task_executions = 0;
	for (int si = 0; si < configuration->stages_number; ++si) {
		configured_task_executions += processed_objects * (configuration->stage_definitions[si].input_count + configuration->stage_definitions[si].output_count);
	}
	double avg_compression = configured_task_executions > 0 ? total_compression / (double)configured_task_executions : 0.0;
	double avg_hashing = configured_task_executions > 0 ? total_hashing / (double)configured_task_executions : 0.0;
	double avg_crypto = configured_task_executions > 0 ? total_crypto / (double)configured_task_executions : 0.0;
	double pipeline_avg_input_size = avg_object_size(total_input_size, processed_objects);
	double pipeline_avg_output_size = avg_object_size(total_output_size, processed_objects);
	printf("Objects=%ld total_input_bytes=%ld avg_input_object_bytes=%f total_output_bytes=%ld avg_output_object_bytes=%f chained_stages=%d configured_task_executions=%ld\n",
		processed_objects, total_input_size, pipeline_avg_input_size, total_output_size, pipeline_avg_output_size, configuration->stages_number, configured_task_executions);
	printf("Compression/decompression total_time=%f s avg_per_task=%f s\n", total_compression, avg_compression);
	printf("Hash calculation/verification total_time=%f s avg_per_task=%f s\n", total_hashing, avg_hashing);
	printf("Encrypt/unencrypt total_time=%f s avg_per_task=%f s\n", total_crypto, avg_crypto);
	printf("Network/storage IO total_time=%f s\n", total_io);
	if (pipeline_metrics_csv) {
		fprintf(pipeline_metrics_csv, "metric,total_seconds,avg_per_task_seconds,objects,total_input_bytes,avg_input_object_bytes,total_output_bytes,avg_output_object_bytes,chained_stages,configured_task_executions\n");
		fprintf(pipeline_metrics_csv, "compression_decompression,%f,%f,%ld,%ld,%f,%ld,%f,%d,%ld\n", total_compression, avg_compression, processed_objects, total_input_size, pipeline_avg_input_size, total_output_size, pipeline_avg_output_size, configuration->stages_number, configured_task_executions);
		fprintf(pipeline_metrics_csv, "hash_calculation_verification,%f,%f,%ld,%ld,%f,%ld,%f,%d,%ld\n", total_hashing, avg_hashing, processed_objects, total_input_size, pipeline_avg_input_size, total_output_size, pipeline_avg_output_size, configuration->stages_number, configured_task_executions);
		fprintf(pipeline_metrics_csv, "encrypt_unencrypt,%f,%f,%ld,%ld,%f,%ld,%f,%d,%ld\n", total_crypto, avg_crypto, processed_objects, total_input_size, pipeline_avg_input_size, total_output_size, pipeline_avg_output_size, configuration->stages_number, configured_task_executions);
		fprintf(pipeline_metrics_csv, "network_storage_io,%f,%f,%ld,%ld,%f,%ld,%f,%d,%ld\n", total_io, 0.0, processed_objects, total_input_size, pipeline_avg_input_size, total_output_size, pipeline_avg_output_size, configuration->stages_number, configured_task_executions);
	}

	if (worker_stage_csv) fclose(worker_stage_csv);
	if (worker_stage_nfr_csv) fclose(worker_stage_nfr_csv);
	if (stage_nfr_totals_csv) fclose(stage_nfr_totals_csv);
	if (stage_totals_csv) fclose(stage_totals_csv);
	if (worker_totals_csv) fclose(worker_totals_csv);
	if (pipeline_metrics_csv) fclose(pipeline_metrics_csv);
	if (worker_workload_csv) fclose(worker_workload_csv);
	if (worker_input_workload_csv) fclose(worker_input_workload_csv);
	if (stage_requirements_csv) fclose(stage_requirements_csv);

	for (int i = 0; i < configuration->workers; ++i) {
		free(arrayWorkers[i].trace);
	}
	free(arrayWorkers);
	free(traceData);
	free(configuration->traces_fileName);
	free(configuration);

	return 0;
}
