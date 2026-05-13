#include "proxy.h"
#include <unistd.h>

/* NFR manager instances (one input + one output manager per stage index 1..10 mapped to 0..9) */
static struct nfr_manager nfr_managers_in[10];
static struct nfr_manager nfr_managers_out[10];
static int nfr_initialized = 0;
/* link stats lock */
static pthread_mutex_t link_stats_lock = PTHREAD_MUTEX_INITIALIZER;
/* Global configuration pointer to inspect stage order for chaining */
static struct config *global_config = NULL;
/* outstanding jobs counter and sync */
static long outstanding_jobs = 0;
static pthread_mutex_t outstanding_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t outstanding_cond = PTHREAD_COND_INITIALIZER;

static void inc_outstanding()
{
    pthread_mutex_lock(&outstanding_lock);
    outstanding_jobs++;
    pthread_mutex_unlock(&outstanding_lock);
}

static void dec_outstanding()
{
    pthread_mutex_lock(&outstanding_lock);
    if (outstanding_jobs > 0) outstanding_jobs--;
    if (outstanding_jobs == 0)
        pthread_cond_broadcast(&outstanding_cond);
    pthread_mutex_unlock(&outstanding_lock);
}

/* Compute network transfer time (seconds) for transferring worker's data from machine `from_mid` to `to_mid`.
   Returns 0.0 if same machine or no data to transfer. Looks up direct link in configuration->links.
*/
static double compute_network_transfer_time(struct worker *w, int from_mid, int to_mid)
{
    if (!w || from_mid == to_mid) return 0.0;
    if (!global_config) return 0.0;

    /* determine total bytes to transfer */
    double total_bytes = (double)w->sizeStorage;
    if (total_bytes <= 0.0) return 0.0;

    const char *from_name = "";
    const char *to_name = "";
    if (from_mid >= 0 && from_mid < global_config->machines_number) from_name = global_config->machines[from_mid].name;
    if (to_mid >= 0 && to_mid < global_config->machines_number) to_name = global_config->machines[to_mid].name;

    /* search for a link matching from->to (directional). If not found, try reverse direction. */
    double b_net = 0.0; /* bytes/sec */
    double latency_ms = 0.0;

    /* search for a link matching from->to (directional). If not found, try reverse direction. */
    for (int li = 0; li < global_config->links_number; ++li)
    {
        if (strcmp(global_config->links[li].from, from_name) == 0 && strcmp(global_config->links[li].to, to_name) == 0)
        {
            b_net = global_config->links[li].b_net;
            latency_ms = global_config->links[li].latency_ms;
            break;
        }
    }
    if (b_net <= 0.0)
    {
        /* try reverse link */
        for (int li = 0; li < global_config->links_number; ++li)
        {
            if (strcmp(global_config->links[li].from, to_name) == 0 && strcmp(global_config->links[li].to, from_name) == 0)
            {
                b_net = global_config->links[li].b_net;
                latency_ms = global_config->links[li].latency_ms;
                break;
            }
        }
    }

    if (b_net <= 0.0)
    {
        /* fallback to conservative default 10 MB/s */
        b_net = 10.0 * 1048576.0;
        latency_ms = 0.0;
    }

    double t_transfer = total_bytes / b_net; /* seconds */
    double t_latency = latency_ms / 1000.0;
    return t_transfer + t_latency;
}

static void *nfr_worker_thread(void *arg)
{
        struct nfr_manager *m = (struct nfr_manager *)arg;

        while (1)
        {
            pthread_mutex_lock(&m->lock);
            while (m->q_count == 0 && !m->stop)
                pthread_cond_wait(&m->cond_nonempty, &m->lock);

            if (m->stop && m->q_count == 0)
            {
                pthread_mutex_unlock(&m->lock);
                break;
            }

            /* dequeue */
            struct nfr_job job = m->queue[m->q_head];
            m->q_head = (m->q_head + 1) % m->q_size;
            m->q_count--;
            pthread_cond_signal(&m->cond_nonfull);
            pthread_mutex_unlock(&m->lock);

            if (job.w)
            {
                /* set worker stage and process */
                job.w->stage = m->stage;
                struct timespec t0, t1;
                clock_gettime(CLOCK_MONOTONIC, &t0);

                serviceTime(job.w);

                clock_gettime(CLOCK_MONOTONIC, &t1);
                double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;

                /* update manager metrics */
                pthread_mutex_lock(&m->lock);
                m->jobs_processed += 1;
                m->total_processing_time += elapsed;
                pthread_mutex_unlock(&m->lock);

                /* If this was the input pipeline, forward job to output pipeline of same stage */
                if (m->is_input)
                {
                    int idx = m->stage - 1;
                    if (idx >= 0 && idx < 10)
                    {
                        /* best-effort enqueue to output manager */
                        nfr_manager_enqueue(&nfr_managers_out[idx], job.w);
                    }
                }
                else
                {
                    /* Output pipeline finished: chain to next configured stage (if any) */
                    if (global_config != NULL)
                    {
                        /* find current stage index in configuration->stages */
                        int found = -1;
                        for (int si = 0; si < global_config->stages_number; ++si)
                        {
                            if (global_config->stages[si] == m->stage)
                            {
                                found = si;
                                break;
                            }
                        }
                        if (found >= 0 && found + 1 < global_config->stages_number)
                        {
                            int nextStage = global_config->stages[found + 1];
                            int nidx = nextStage - 1;
                            if (nidx >= 0 && nidx < 10 && nfr_managers_in[nidx].threads != NULL)
                            {
                                /* compute network transfer time if machines differ */
                                int from_mid = job.w->machine_id;
                                int to_mid = -1;
                                /* find machine hosting nextStage */
                                for (int mm = 0; mm < global_config->machines_number; ++mm)
                                {
                                    for (int s = 0; s < global_config->machines[mm].stages_number; ++s)
                                    {
                                        if (global_config->machines[mm].stages[s] == nextStage)
                                        {
                                            to_mid = mm;
                                            break;
                                        }
                                    }
                                    if (to_mid >= 0) break;
                                }

                                double net_t = compute_network_transfer_time(job.w, from_mid, to_mid);
                                if (net_t > 0.0)
                                {
                                    /* distribute network time across traces proportionally */
                                    double total = (double)job.w->sizeStorage;
                                    if (total > 0.0)
                                    {
                                        for (int tj = 0; tj < job.w->sizeWorker; ++tj)
                                        {
                                            double frac = (double)job.w->trace[tj].size / total;
                                            job.w->trace[tj].service_time_io += (float)(net_t * frac);
                                        }
                                    }

                                    /* update link metrics if a matching link exists (only if from/to machine indices valid) */
                                    if (from_mid >= 0 && to_mid >= 0 && from_mid < global_config->machines_number && to_mid < global_config->machines_number)
                                    {
                                        const char *from_name = global_config->machines[from_mid].name;
                                        const char *to_name = global_config->machines[to_mid].name;
                                        pthread_mutex_lock(&link_stats_lock);
                                        for (int li = 0; li < global_config->links_number; ++li)
                                        {
                                            if ((strcmp(global_config->links[li].from, from_name) == 0 && strcmp(global_config->links[li].to, to_name) == 0) ||
                                                (strcmp(global_config->links[li].from, to_name) == 0 && strcmp(global_config->links[li].to, from_name) == 0))
                                            {
                                                global_config->links[li].bytes_transferred += (double)job.w->sizeStorage;
                                                global_config->links[li].transfers_count += 1;
                                                global_config->links[li].total_transfer_time += net_t;
                                                break;
                                            }
                                        }
                                        pthread_mutex_unlock(&link_stats_lock);
                                    }

                                    /* simulate transfer delay */
                                    usleep((useconds_t)(net_t * 1e6));
                                }

                                nfr_manager_enqueue(&nfr_managers_in[nidx], job.w);
                            }
                        }
                        else
                        {
                            /* no next stage: this is final output, decrement outstanding jobs */
                            dec_outstanding();
                        }
                    }
                }
            }
        }

        return NULL;
    }

int nfr_manager_init(struct nfr_manager *m, int stage, int num_threads, int q_size, int is_input)
{
    m->stage = stage;
    m->num_threads = num_threads > 0 ? num_threads : 1;
    m->q_size = q_size > 0 ? q_size : 1024;
    m->queue = malloc(sizeof(struct nfr_job) * m->q_size);
    if (!m->queue) return -1;
    m->q_head = m->q_tail = m->q_count = 0;
    m->stop = 0;
    m->is_input = is_input ? 1 : 0;
    m->jobs_processed = 0;
    m->total_processing_time = 0.0;
    pthread_mutex_init(&m->lock, NULL);
    pthread_cond_init(&m->cond_nonempty, NULL);
    pthread_cond_init(&m->cond_nonfull, NULL);
    m->threads = malloc(sizeof(pthread_t) * m->num_threads);
    if (!m->threads) { free(m->queue); return -1; }

    for (int i = 0; i < m->num_threads; ++i)
        pthread_create(&m->threads[i], NULL, nfr_worker_thread, (void *)m);

    return 0;
}

int nfr_manager_enqueue(struct nfr_manager *m, struct worker *w)
{
    if (!m || !w) return -1;
    printf("[ENQUEUE] Stage %d %s worker %d\n", m->stage, m->is_input ? "IN" : "OUT", w->id);
    pthread_mutex_lock(&m->lock);
    while (m->q_count == m->q_size && !m->stop)
        pthread_cond_wait(&m->cond_nonfull, &m->lock);
    if (m->stop)
    {
        pthread_mutex_unlock(&m->lock);
        return -1;
    }
    m->queue[m->q_tail].w = w;
    m->q_tail = (m->q_tail + 1) % m->q_size;
    m->q_count++;
    pthread_cond_signal(&m->cond_nonempty);
    pthread_mutex_unlock(&m->lock);
    return 0;
}

void nfr_manager_shutdown(struct nfr_manager *m)
{
    pthread_mutex_lock(&m->lock);
    m->stop = 1;
    pthread_cond_broadcast(&m->cond_nonempty);
    pthread_cond_broadcast(&m->cond_nonfull);
    pthread_mutex_unlock(&m->lock);

    for (int i = 0; i < m->num_threads; ++i)
        pthread_join(m->threads[i], NULL);

    free(m->threads);
    free(m->queue);
    pthread_mutex_destroy(&m->lock);
    pthread_cond_destroy(&m->cond_nonempty);
    pthread_cond_destroy(&m->cond_nonfull);
}

void shutdown_and_report_metrics(struct config *configuration)
{
    printf("\nShutting down NFR managers...\n");
    if (!configuration) return;

    /* First: request stop on all managers and wake them so they don't enqueue to freed queues. */
    for (int si = 0; si < configuration->stages_number; ++si)
    {
        int sn = configuration->stages[si] - 1;
        if (sn >= 0 && sn < 10)
        {
            if (nfr_managers_in[sn].threads)
            {
                pthread_mutex_lock(&nfr_managers_in[sn].lock);
                nfr_managers_in[sn].stop = 1;
                pthread_cond_broadcast(&nfr_managers_in[sn].cond_nonempty);
                pthread_cond_broadcast(&nfr_managers_in[sn].cond_nonfull);
                pthread_mutex_unlock(&nfr_managers_in[sn].lock);
            }
            if (nfr_managers_out[sn].threads)
            {
                pthread_mutex_lock(&nfr_managers_out[sn].lock);
                nfr_managers_out[sn].stop = 1;
                pthread_cond_broadcast(&nfr_managers_out[sn].cond_nonempty);
                pthread_cond_broadcast(&nfr_managers_out[sn].cond_nonfull);
                pthread_mutex_unlock(&nfr_managers_out[sn].lock);
            }
        }
    }

    /* Second: join threads and free resources for all managers. */
    for (int si = 0; si < configuration->stages_number; ++si)
    {
        int sn = configuration->stages[si] - 1;
        if (sn >= 0 && sn < 10)
        {
            if (nfr_managers_in[sn].threads) nfr_manager_shutdown(&nfr_managers_in[sn]);
            if (nfr_managers_out[sn].threads) nfr_manager_shutdown(&nfr_managers_out[sn]);
        }
    }

    printf("\n=== Manager Metrics ===\n");
    for (int si = 0; si < configuration->stages_number; ++si)
    {
        int sn = configuration->stages[si] - 1;
        if (sn >= 0 && sn < 10)
        {
            long in_jobs = nfr_managers_in[sn].jobs_processed;
            double in_time = nfr_managers_in[sn].total_processing_time;
            double in_avg = (in_jobs > 0) ? (in_time / (double)in_jobs) : 0.0;

            long out_jobs = nfr_managers_out[sn].jobs_processed;
            double out_time = nfr_managers_out[sn].total_processing_time;
            double out_avg = (out_jobs > 0) ? (out_time / (double)out_jobs) : 0.0;

            double combined_time = in_time + out_time;
            long combined_jobs = in_jobs + out_jobs;
            double combined_avg = (combined_jobs > 0) ? (combined_time / (double)combined_jobs) : 0.0;

            printf("Stage %d IN: jobs=%ld total_time=%f s avg_per_job=%f s\n", configuration->stages[si], in_jobs, in_time, in_avg);
            printf("Stage %d OUT: jobs=%ld total_time=%f s avg_per_job=%f s\n", configuration->stages[si], out_jobs, out_time, out_avg);
            printf("Stage %d COMBINED: jobs=%ld total_time=%f s avg_per_job=%f s\n", configuration->stages[si], combined_jobs, combined_time, combined_avg);
        }
    }

    printf("\n=== Link Metrics ===\n");
    for (int li = 0; li < configuration->links_number; ++li)
    {
        printf("Link %s->%s transfers=%d bytes=%f total_time=%f s\n", configuration->links[li].from, configuration->links[li].to, configuration->links[li].transfers_count, configuration->links[li].bytes_transferred, configuration->links[li].total_transfer_time);
    }
}

/* Wait until all NFR manager queues are drained or timeout (seconds). */
int wait_for_managers_empty(struct config *configuration, int timeout_seconds)
{
    if (!configuration) return -1;
    int waited = 0;
    int stable_count = 0;
    long last_total = -1;
    while (waited < timeout_seconds)
    {
        long total_q = 0;
        for (int si = 0; si < configuration->stages_number; ++si)
        {
            int sn = configuration->stages[si] - 1;
            if (sn >= 0 && sn < 10)
            {
                pthread_mutex_lock(&nfr_managers_in[sn].lock);
                total_q += nfr_managers_in[sn].q_count;
                pthread_mutex_unlock(&nfr_managers_in[sn].lock);
                pthread_mutex_lock(&nfr_managers_out[sn].lock);
                total_q += nfr_managers_out[sn].q_count;
                pthread_mutex_unlock(&nfr_managers_out[sn].lock);
            }
        }
        if (total_q == 0)
        {
            if (last_total == 0)
            {
                stable_count++;
            }
            else
            {
                stable_count = 0;
            }
            last_total = 0;
            if (stable_count >= 3)
                return 0; /* drained */
        }
        else
        {
            last_total = total_q;
            stable_count = 0;
        }
        usleep(100 * 1000); /* 100 ms */
        waited += 0.1;
    }
    return -1; /* timeout */
}

int wait_for_outstanding_zero(int timeout_seconds)
{
    struct timespec ts;
    pthread_mutex_lock(&outstanding_lock);
    if (outstanding_jobs == 0)
    {
        pthread_mutex_unlock(&outstanding_lock);
        return 0;
    }
    /* compute absolute timeout */
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_sec += timeout_seconds;
    int rc = 0;
    while (outstanding_jobs > 0 && rc == 0)
    {
        rc = pthread_cond_timedwait(&outstanding_cond, &outstanding_lock, &ts);
    }
    pthread_mutex_unlock(&outstanding_lock);
    return (outstanding_jobs == 0) ? 0 : -1;
}


/**
 * @brief Function returns error in the case to occur.
 */
void error(const char *s)
{
    perror(s); //< perror() returns the S string and the error that found in errno.
    exit(EXIT_FAILURE);
}

/**
 * @brief Function that read the JSON config file.
 */
char agent_container_prefix[32] = "output_agent";

struct config *read_config(char *file_name)
{
    FILE *file;
    struct config *configuration;
    long length;
    char *data;
    cJSON *json, *workers, *traces_number, *traces_fileName, *agent_type, *stages, *stage;

    configuration = malloc(sizeof(struct config));
    configuration->stages_number = 0;
    configuration->traces_fileName = malloc((255) * sizeof(char));
    strcpy(configuration->agent_type, "output");

    file = fopen(file_name, "rb"); //< Read file
    if (!file)
        error("Error opening config.json");

    fseek(file, 0, SEEK_END);
    length = ftell(file);
    fseek(file, 0, SEEK_SET);
    data = malloc(length + 1);
    if (data)
    {
        fread(data, 1, length, file);
    }
    data[length] = '\0';
    fclose(file);

    json = cJSON_Parse(data);
    if (!json)
    {
        printf("Error before: [%s]\n", cJSON_GetErrorPtr());
        error("Error parsing config.json");
    }

    // Default values
    strcpy(configuration->compression_algo, "");
    strcpy(configuration->hashing_algo, "");
    strcpy(configuration->ida_algo, "");

    workers = cJSON_GetObjectItemCaseSensitive(json, "workers");
    if (cJSON_IsNumber(workers))
    {
        configuration->workers = workers->valueint;
    }

    traces_number = cJSON_GetObjectItemCaseSensitive(json, "traces_number");
    if (cJSON_IsNumber(traces_number))
    {
        configuration->traces_number = traces_number->valueint;
    }

    traces_fileName = cJSON_GetObjectItemCaseSensitive(json, "traces_fileName");
    if (cJSON_IsString(traces_fileName) && (traces_fileName->valuestring != NULL))
    {
        strcpy(configuration->traces_fileName, traces_fileName->valuestring);
    }

    agent_type = cJSON_GetObjectItemCaseSensitive(json, "agent_type");
    if (cJSON_IsString(agent_type) && (agent_type->valuestring != NULL))
    {
        if (strcmp(agent_type->valuestring, "input") == 0 || strcmp(agent_type->valuestring, "output") == 0)
        {
            strcpy(configuration->agent_type, agent_type->valuestring);
        }
        else
        {
            strcpy(configuration->agent_type, "output");
        }
    }

    cJSON *comp_algo = cJSON_GetObjectItemCaseSensitive(json, "compression_algo");
    if (cJSON_IsString(comp_algo) && (comp_algo->valuestring != NULL))
    {
        strcpy(configuration->compression_algo, comp_algo->valuestring);
    }

    cJSON *hash_algo = cJSON_GetObjectItemCaseSensitive(json, "hashing_algo");
    if (cJSON_IsString(hash_algo) && (hash_algo->valuestring != NULL))
    {
        strcpy(configuration->hashing_algo, hash_algo->valuestring);
    }

    cJSON *id_algo = cJSON_GetObjectItemCaseSensitive(json, "ida_algo");
    if (cJSON_IsString(id_algo) && (id_algo->valuestring != NULL))
    {
        strcpy(configuration->ida_algo, id_algo->valuestring);
    }

    cJSON *id_k = cJSON_GetObjectItemCaseSensitive(json, "ida_k");
    if (cJSON_IsNumber(id_k))
    {
        configuration->ida_k = id_k->valueint;
    }
    else
    {
        configuration->ida_k = 4; // Default
    }

    cJSON *id_m = cJSON_GetObjectItemCaseSensitive(json, "ida_m");
    if (cJSON_IsNumber(id_m))
    {
        configuration->ida_m = id_m->valueint;
    }
    else
    {
        configuration->ida_m = 2; // Default
    }

    cJSON *bfs = cJSON_GetObjectItemCaseSensitive(json, "b_fs");
    if (cJSON_IsNumber(bfs)) {
        /* configuration file provides b_fs in MB/s — convert to bytes/sec */
        configuration->b_fs = bfs->valuedouble * 1048576.0;
    } else {
        configuration->b_fs = 100.0 * 1048576.0; // Default 100 MB/s
    }
    {
        configuration->b_fs = 104857600.0; // Default 100 MB/s
    }

    stages = cJSON_GetObjectItemCaseSensitive(json, "stages");
    if (cJSON_IsArray(stages))
    {
        cJSON_ArrayForEach(stage, stages)
        {
            if (cJSON_IsString(stage) && configuration->stages_number < 10)
            {
                if (strcmp(stage->valuestring, "compress") == 0)
                {
                    configuration->stages[configuration->stages_number++] = 1;
                }
                else if (strcmp(stage->valuestring, "hashing") == 0)
                {
                    configuration->stages[configuration->stages_number++] = 2;
                }
                else if (strcmp(stage->valuestring, "indexing") == 0)
                {
                    configuration->stages[configuration->stages_number++] = 3;
                }
                else if (strcmp(stage->valuestring, "dispersal") == 0)
                {
                    configuration->stages[configuration->stages_number++] = 4;
                }
                else if (strcmp(stage->valuestring, "upload") == 0)
                {
                    configuration->stages[configuration->stages_number++] = 5;
                }
            }
        }
    }

        /* Optional distributed machines specification
           Format: "machines": [ {"name":"m0","stages":["compress","hashing"]}, ... ]
        */
        configuration->machines_number = 0;
        cJSON *machines = cJSON_GetObjectItemCaseSensitive(json, "machines");
        if (cJSON_IsArray(machines)) {
            cJSON *m;
            int m_idx = 0;
            cJSON_ArrayForEach(m, machines) {
                if (m_idx >= MAX_MACHINES) break;
                cJSON *mname = cJSON_GetObjectItemCaseSensitive(m, "name");
                cJSON *mstages = cJSON_GetObjectItemCaseSensitive(m, "stages");
                if (cJSON_IsString(mname) && mname->valuestring) {
                    strncpy(configuration->machines[m_idx].name, mname->valuestring, sizeof(configuration->machines[m_idx].name)-1);
                    configuration->machines[m_idx].name[sizeof(configuration->machines[m_idx].name)-1] = '\0';
                } else {
                    snprintf(configuration->machines[m_idx].name, sizeof(configuration->machines[m_idx].name), "machine%d", m_idx);
                }
                configuration->machines[m_idx].stages_number = 0;
                if (cJSON_IsArray(mstages)) {
                    cJSON *ms;
                    cJSON_ArrayForEach(ms, mstages) {
                        if (cJSON_IsString(ms) && configuration->machines[m_idx].stages_number < 10) {
                            const char *sv = ms->valuestring;
                            if (strcmp(sv, "compress") == 0) configuration->machines[m_idx].stages[configuration->machines[m_idx].stages_number++] = 1;
                            else if (strcmp(sv, "hashing") == 0) configuration->machines[m_idx].stages[configuration->machines[m_idx].stages_number++] = 2;
                            else if (strcmp(sv, "indexing") == 0) configuration->machines[m_idx].stages[configuration->machines[m_idx].stages_number++] = 3;
                            else if (strcmp(sv, "dispersal") == 0) configuration->machines[m_idx].stages[configuration->machines[m_idx].stages_number++] = 4;
                            else if (strcmp(sv, "upload") == 0) configuration->machines[m_idx].stages[configuration->machines[m_idx].stages_number++] = 5;
                        }
                    }
                }
                m_idx++;
            }
            configuration->machines_number = m_idx;
        }

        /* Optional network links between machines: {"from":"m0","to":"m1","b_net":100} (MB/s) */
        configuration->links_number = 0;
        cJSON *links = cJSON_GetObjectItemCaseSensitive(json, "links");
        if (cJSON_IsArray(links)) {
            cJSON *l;
            int l_idx = 0;
            cJSON_ArrayForEach(l, links) {
                if (l_idx >= MAX_LINKS) break;
                cJSON *from = cJSON_GetObjectItemCaseSensitive(l, "from");
                cJSON *to = cJSON_GetObjectItemCaseSensitive(l, "to");
                cJSON *bnet = cJSON_GetObjectItemCaseSensitive(l, "b_net");
                cJSON *lat = cJSON_GetObjectItemCaseSensitive(l, "latency_ms");
                if (cJSON_IsString(from) && from->valuestring) {
                    strncpy(configuration->links[l_idx].from, from->valuestring, sizeof(configuration->links[l_idx].from)-1);
                    configuration->links[l_idx].from[sizeof(configuration->links[l_idx].from)-1] = '\0';
                } else configuration->links[l_idx].from[0] = '\0';
                if (cJSON_IsString(to) && to->valuestring) {
                    strncpy(configuration->links[l_idx].to, to->valuestring, sizeof(configuration->links[l_idx].to)-1);
                    configuration->links[l_idx].to[sizeof(configuration->links[l_idx].to)-1] = '\0';
                } else configuration->links[l_idx].to[0] = '\0';
                configuration->links[l_idx].b_net = 0.0;
                if (cJSON_IsNumber(bnet)) configuration->links[l_idx].b_net = bnet->valuedouble * 1048576.0; /* MB/s -> bytes/sec */
                configuration->links[l_idx].latency_ms = 0.0;
                if (cJSON_IsNumber(lat)) configuration->links[l_idx].latency_ms = lat->valuedouble;
                l_idx++;
            }
            configuration->links_number = l_idx;
        }

    cJSON_Delete(json);
    free(data);
    return configuration;
}

struct traceConfig *read_configTrace(int numberTrace, char *fileName)
{
    FILE *file;
    char *token, *delimitador, line[500], key[200], value[400];
    int linenum, cont, i;
    struct traceConfig *traceData;

    traceData = malloc(sizeof(struct traceConfig) * numberTrace);
    token = NULL;
    delimitador = " ";

    if (!traceData)
        error("Memory allocation failed for traceData");

    linenum = 0;
    file = fopen(fileName, "r"); //< Read file
    if (!file)
        error("Error opening trace configuration file");
    cont = 0;
    i = 1;

    while (fgets(line, 256, file) != NULL)
    {
        linenum++;
        if (line[0] == '#')
            continue;

        delimitador = " ";
        token = strtok(line, delimitador);

        if (token != NULL)
        {
            while (token != NULL)
            {
                switch (i)
                {

                case 1:
                    strcpy(value, token);
                    traceData[cont].MUESTRAS = atoi(value);
                    break;
                case 2:
                    strcpy(value, token);
                    traceData[cont].inter_arrival = atof(value);
                    break;
                case 3:
                    strcpy(value, token);
                    traceData[cont].DISTRIBUTION = atoi(value);
                    break;
                case 4:
                    strcpy(value, token);
                    traceData[cont].mean = atof(value);
                    break;
                case 5:
                    strcpy(value, token);
                    traceData[cont].stddev = atof(value);
                    break;
                case 6:
                    strcpy(value, token);
                    traceData[cont].SIZE = atof(value);
                    break;
                case 7:
                    strcpy(value, token);
                    traceData[cont].stddevS = atof(value);
                    break;
                case 8:
                    strcpy(value, token);
                    traceData[cont].Concurrency = atoi(value);
                    break;
                }

                token = strtok(NULL, delimitador);

                i++;

                if (i > 8)
                {
                    i = 1;
                }
            }
        }
        cont++;
        if (cont >= numberTrace)
            break;
    }
    fclose(file);
    return traceData;
}

void makeTraceGenerator()
{
    char *command, *pwd;
    int size;

    pwd = getenv("PWD");
    // Increased size slightly to accommodate the check logic
    size = 500 + (strlen(pwd) * 2);
    command = malloc(size * sizeof(char));

    // Check if container exists; if not, run it
    sprintf(
        command,
        "docker ps -a --format '{{.Names}}' | grep -Eq '^trace_generator$' || "
        "docker run -i -d --name trace_generator -v '%s/traces/':'%s' trace:generator",
        pwd, pwd);

    execute_command(command);
    free(command);
}

void makeAgents(int workers, const char *agent_type)
{
    char *command, *pwd;
    int i;
    const char *container_prefix = strcmp(agent_type, "input") == 0 ? "input_agent" : "output_agent";

    pwd = getenv("PWD");

    for (i = 0; i < workers; ++i)
    {
        // Allocated slightly more buffer for the shell logic
        int size = 500 + (strlen(pwd) * 2);
        command = malloc(size * sizeof(char));

        sprintf(
            command,
            "docker ps -a --format '{{.Names}}' | grep -Eq '^%s%d$' || "
            "docker run -i -d --name %s%d -v '%s/traces/':'%s' single:queue",
            container_prefix, i,
            container_prefix, i,
            pwd, pwd);

        execute_command(command);
        free(command);
    }
}

void makeContainers(struct config *configuration)
{
    makeTraceGenerator();
    makeAgents(configuration->workers, configuration->agent_type);
}

void traceGenerator(struct traceConfig *traceData, int numberTraces)
{
    char *fileName, *command, *pwd;
    const char *baseName = "trace%lld.txt";
    const char *baseCommand = "docker exec trace_generator ./main %lld %f %lld %f %f %f %f %lld > '%s/traces/%s'";
    int i;

    pwd = getenv("PWD");

    for (i = 0; i < numberTraces; ++i)
    {

        fileName = malloc(sizeof(char) + strlen(baseName) + 100);
        sprintf(fileName,
                baseName, i);

        command = malloc(sizeof(char) + (strlen(baseCommand) + strlen(pwd) + strlen(fileName) + (14 * 8)));
        sprintf(command,
                baseCommand,
                traceData[i].MUESTRAS,
                traceData[i].inter_arrival,
                traceData[i].DISTRIBUTION,
                traceData[i].mean,
                traceData[i].stddev,
                traceData[i].SIZE,
                traceData[i].stddevS,
                traceData[i].Concurrency,
                pwd, fileName);

        execute_command(command);

        free(command);
        free(fileName);
    }
}

void execute_command(char *command)
{
    FILE *fp;
    fp = popen(command, "r");
    if (fp == NULL)
    {
        printf("Failed to run command to execute trace_generator container\n");
        exit(1);
    }
    pclose(fp);
}

/**
 * @brief Function that allows to obtain size of the file.
 */
long fileSize(char *fname)
{
    long ftam = -1;
    struct stat fdata;
    int error;

    ftam = -1;
    error = stat(fname, &fdata);
    if (error >= 0)
        ftam = fdata.st_size;
    else
        printf("FileName: %s ERRNO: %d - %s\n", fname, errno, strerror(errno));

    return ftam;
}

/**
 * @brief Function to load balanced.
 */
struct worker *assignation(struct config *configuration, struct traceConfig *traceData)
{
    struct worker *arrayWorkers;
    struct traces *traces;
    const char *baseName;
    char *fileName, *pwd;
    int i, position, position1, position2;
    int *contar;
    FILE *fp;
    char line[256];
    long long unsigned interarrival, size;

    arrayWorkers = (struct worker *)malloc(configuration->workers * sizeof(struct worker));
    contar = malloc(configuration->workers * sizeof(int));

    for (int i = 0; i < configuration->workers; ++i)
    {
        contar[i] = 0;
        arrayWorkers[i].id = i;
        arrayWorkers[i].sizeWorker = 0;
        arrayWorkers[i].sizeStorage = 0;
        arrayWorkers[i].interarrive = 0;
        strncpy(arrayWorkers[i].agent_type, configuration->agent_type, sizeof(arrayWorkers[i].agent_type) - 1);
        arrayWorkers[i].agent_type[sizeof(arrayWorkers[i].agent_type) - 1] = '\0';
        arrayWorkers[i].b_fs = configuration->b_fs;
        arrayWorkers[i].trace = malloc(sizeof(struct traces) * traceData[0].MUESTRAS);
        /* initialize trace entries to safe defaults to avoid garbage values */
        if (arrayWorkers[i].trace) {
            for (int ti = 0; ti < traceData[0].MUESTRAS; ++ti) {
                arrayWorkers[i].trace[ti].traceName = NULL;
                arrayWorkers[i].trace[ti].size = 0;
                arrayWorkers[i].trace[ti].mean_interarrival = 0.0f;
                arrayWorkers[i].trace[ti].service_time_c = 0.0f;
                arrayWorkers[i].trace[ti].service_time_h = 0.0f;
                arrayWorkers[i].trace[ti].service_time_idx = 0.0f;
                arrayWorkers[i].trace[ti].service_time_ida = 0.0f;
                arrayWorkers[i].trace[ti].service_time_io = 0.0f;
                arrayWorkers[i].trace[ti].MUESTRAS = 0;
            }
        }
        arrayWorkers[i].service_time = 0.0f;

        /* Assign a permanent stage owner to the worker (round-robin across configured stages).
           This groups workers by stage; each stage is expected to be deployed on a single machine. */
        if (configuration->stages_number > 0)
            arrayWorkers[i].stage_owner = configuration->stages[i % configuration->stages_number];
        else
            arrayWorkers[i].stage_owner = 1;

        /* Determine the machine hosting the worker's stage (if machines are configured). */
        arrayWorkers[i].machine_id = -1;
        if (configuration->machines_number > 0)
        {
            for (int mid = 0; mid < configuration->machines_number; ++mid)
            {
                for (int s = 0; s < configuration->machines[mid].stages_number; ++s)
                {
                    if (configuration->machines[mid].stages[s] == arrayWorkers[i].stage_owner)
                    {
                        arrayWorkers[i].machine_id = mid;
                        break;
                    }
                }
                if (arrayWorkers[i].machine_id >= 0)
                    break;
            }
        }
    }

    /* Initialize NFR managers for each configured stage (one manager per stage index).
       Number of NFR worker threads per manager is proportional to workers/stages. */
    if (!nfr_initialized)
    {
        int per_stage_threads = configuration->workers / (configuration->stages_number > 0 ? configuration->stages_number : 1);
        if (per_stage_threads < 1) per_stage_threads = 1;
        for (int si = 0; si < configuration->stages_number; ++si)
        {
            int stageNum = configuration->stages[si];
            int idx = stageNum - 1;
            nfr_manager_init(&nfr_managers_in[idx], stageNum, per_stage_threads, 1024, 1);
            nfr_manager_init(&nfr_managers_out[idx], stageNum, per_stage_threads, 1024, 0);
        }
        nfr_initialized = 1;
    }

    /* expose configuration globally for chaining */
    global_config = configuration;

    srand(time(NULL));

    pwd = getenv("PWD");
    baseName = "%s/traces/trace0.txt";

    fileName = malloc(sizeof(char) + strlen(baseName) + strlen(pwd) + 50);
    sprintf(fileName, baseName, pwd);

    traces = malloc(sizeof(struct traces) * traceData[0].MUESTRAS);

    fp = fopen(fileName, "r");
    if (fp == NULL)
    {
        printf("Error opening trace file %s\n", fileName);
        exit(1);
    }

    i = 0;
    while (fgets(line, sizeof(line), fp) != NULL && i < traceData[0].MUESTRAS)
    {
        if (sscanf(line, "%llu %llu", &interarrival, &size) == 2)
        {
            traces[i].traceName = "object";
            traces[i].size = size;
            traces[i].mean_interarrival = traceData[0].inter_arrival; // use configured mean
            traces[i].MUESTRAS = 1;

            position1 = rand() % configuration->workers;
            position2 = rand() % configuration->workers;

            if (configuration->workers == 1)
                position = position1;
            else
            {
                while (position1 == position2)
                    position2 = rand() % configuration->workers;

                if (arrayWorkers[position1].sizeStorage > arrayWorkers[position2].sizeStorage)
                    position = position2;
                else
                    position = position1;
            }

            arrayWorkers[position].sizeStorage += traces[i].size;
            arrayWorkers[position].sizeWorker++;
            arrayWorkers[position].trace[contar[position]] = traces[i];
            contar[position]++;

            i++;
        }
    }
    fclose(fp);
    free(contar);
    free(fileName);
    free(traces);

    return arrayWorkers;
}

/**
 * @brief Function that deploy threads in the pattern.
 */
void deployThread_stages(struct config *configuration, struct worker *arrayWorkers, int stageNumber)
{
    int rc, i;
    char *command;
    FILE *fp;
    pthread_t threads[configuration->workers]; //< thread handles
    int created_indices[configuration->workers];
    int created_count = 0;

    /* Determine which workers should run this stage.
       Only workers whose `stage_owner` matches `stageNumber` will run it.
       If machines are configured, the worker's machine must host the stage as well. */
    for (i = 0; i < configuration->workers; i++)
    {
        int should_run = 0;
        if (arrayWorkers[i].stage_owner != stageNumber)
        {
            should_run = 0;
        }
        else if (configuration->machines_number > 0)
        {
            int mid = arrayWorkers[i].machine_id;
            if (mid >= 0)
            {
                for (int s = 0; s < configuration->machines[mid].stages_number; ++s)
                {
                    if (configuration->machines[mid].stages[s] == stageNumber)
                    {
                        should_run = 1;
                        break;
                    }
                }
            }
        }
        else
        {
            /* No machines configured: local single-node behavior */
            should_run = 1;
        }

        if (should_run)
        {
            /* enqueue work to NFR manager for this stage */
            int idx = stageNumber - 1;
            if (nfr_initialized && nfr_managers_in[idx].threads != NULL)
            {
                nfr_manager_enqueue(&nfr_managers_in[idx], &arrayWorkers[i]);
                /* if this is the first configured stage, count as outstanding job */
                if (configuration && configuration->stages_number > 0 && stageNumber == configuration->stages[0])
                    inc_outstanding();
                /* manager will process job; no direct thread created so do not add to join list */
            }
            else
            {
                /* fallback to direct thread if manager not available */
                arrayWorkers[i].stage = stageNumber;
                rc = pthread_create(&threads[i], NULL, sendWorkstage, (void *)&arrayWorkers[i]);
                if (rc)
                {
                    printf("\tMaster ERROR; return code from pthread_create() is %d\n", rc);
                    exit(-1);
                }
                created_indices[created_count++] = i;
            }
        }
        else
        {
            arrayWorkers[i].stage = 0; /* not running this stage on this worker */
        }
    }

    /* Join only created threads */
    for (i = 0; i < created_count; ++i)
    {
        int idx = created_indices[i];
        pthread_join(threads[idx], NULL);
    }
}

/**
 * @brief Function that send work to workers.
 */
void *sendWorkstage(void *threadarg)
{
    struct worker *my_data;

    my_data = (struct worker *)threadarg; //< Conversion to structure attribute

    serviceTime(my_data);

    printf("Worker %d (machine %d) finished stage %d\n", my_data->id, my_data->machine_id, my_data->stage);

    pthread_exit(NULL); //< Kill thread
}

void serviceTime(struct worker *my_data)
{
    int is_input = (strcmp(my_data->agent_type, "input") == 0);
    printf("Worker %d (machine %d) processing stage %d (%s)\n", my_data->id, my_data->machine_id, my_data->stage, is_input ? "input" : "output");
    switch (my_data->stage)
    {
    case 1: /* compress (output) / decompress (input) */
        if (is_input)
            decompress_time(my_data);
        else
            compress_time(my_data);
        break;
    case 2: /* hashing — same direction for both */
        hashing_time(my_data);
        break;
    case 3: /* indexing — same direction for both */
        indexing_time(my_data);
        break;
    case 4: /* dispersal (output) / reconstruct (input) */
        if (is_input)
            IDA_reconstruct_time(my_data);
        else
            IDA_time(my_data);
        break;
    case 5:
        upload_time(my_data);
        break;
    }
    printf("Worker %d (machine %d) completed stage %d (%s)\n", my_data->id, my_data->machine_id, my_data->stage, is_input ? "input" : "output");
}

void compress_time(struct worker *my_data)
{
    int j;
    char *command, *path;
    float st_sum, st_avg;

    st_sum = 0;
    st_avg = 0;
    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            double t_read = 0.0, t_write = 0.0;
            long unsigned size_before = my_data->trace[j].size;
            if (my_data->b_fs > 0.0)
                t_read = (double)size_before / my_data->b_fs;

            float comp_time = compressStage((double)size_before);
            long unsigned new_size = (long unsigned)compressStageSize((double)size_before);
            if (my_data->b_fs > 0.0)
                t_write = (double)new_size / my_data->b_fs;

            my_data->trace[j].service_time_c = comp_time + (float)(t_read + t_write);
            my_data->trace[j].size = new_size;
            st_sum += my_data->trace[j].service_time_c;
        }
        st_avg = st_sum / my_data->sizeWorker;

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage1.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

void hashing_time(struct worker *my_data)
{
    int j;
    char *command, *path;
    long unsigned newSize;
    float st_sum, st_avg;

    st_sum = 0;
    st_avg = 0;
    newSize = 0;

    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            double t_read = 0.0, t_write = 0.0;
            long unsigned size_before = my_data->trace[j].size;
            if (my_data->b_fs > 0.0)
                t_read = (double)size_before / my_data->b_fs;

            float hash_time = hashingStage((double)size_before);
            long unsigned new_size = (long unsigned)hashingStageSize((double)size_before);
            if (my_data->b_fs > 0.0)
                t_write = (double)new_size / my_data->b_fs;

            my_data->trace[j].service_time_h = hash_time + (float)(t_read + t_write);
            my_data->trace[j].size = new_size;
            st_sum += my_data->trace[j].service_time_h;
        }
        st_avg = st_sum / my_data->sizeWorker;

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage2.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

void indexing_time(struct worker *my_data)
{
    char *command, *path;
    int j;
    float st, st_avg;

    st = 0;
    st_avg = 0;
    my_data->service_time = 0;
    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        st = indexingStage(my_data->sizeWorker);
        st_avg = st / my_data->sizeWorker;

        /* Add I/O: read each object before indexing and write result after indexing */
        double io_total = 0.0;
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            if (my_data->b_fs > 0.0)
            {
                double t_read = (double)my_data->trace[j].size / my_data->b_fs;
                double t_write = (double)my_data->trace[j].size / my_data->b_fs; /* metadata write approx same size */
                io_total += (t_read + t_write);
            }
        }

        st_avg += (float)(io_total / my_data->sizeWorker);

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage3.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        /* record per-worker total and per-object indexing times */
        my_data->service_time = st + (float)io_total;
        for (j = 0; j < my_data->sizeWorker; ++j) {
            my_data->trace[j].service_time_idx = st_avg; /* per-object average for indexing */
        }
        execute_command(command);
        free(command);
    }
}

void IDA_time(struct worker *my_data)
{
    int j;
    char *command, *path;
    long unsigned newSize;
    float st_sum, st_avg;

    st_sum = 0;
    st_avg = 0;
    newSize = 0;

    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            double t_read = 0.0, t_write = 0.0;
            long unsigned size_before = my_data->trace[j].size;
            if (my_data->b_fs > 0.0)
                t_read = (double)size_before / my_data->b_fs;

            float ida_time = IDAStage((double)size_before);
            long unsigned new_size = (long unsigned)IDAStageSize((double)size_before);
            if (my_data->b_fs > 0.0)
                t_write = (double)new_size / my_data->b_fs;

            my_data->trace[j].service_time_ida = ida_time + (float)(t_read + t_write);
            my_data->trace[j].size = new_size;
            st_sum += my_data->trace[j].service_time_ida;
        }
        st_avg = st_sum / my_data->sizeWorker;

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage4.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

/**
 * @brief Input agent: reconstruct (IDA inverse) — shrinks data back.
 * Uses the same interpolated time but applies the inverse size ratio (k/(k+m)).
 */
void IDA_reconstruct_time(struct worker *my_data)
{
    int j;
    char *command, *path;
    float st_sum, st_avg;

    st_sum = 0;
    st_avg = 0;

    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            double t_read = 0.0, t_write = 0.0;
            long unsigned size_before = my_data->trace[j].size;
            if (my_data->b_fs > 0.0)
                t_read = (double)size_before / my_data->b_fs;

            float dec_time = compressStage((double)size_before);
            /* Restore size using existing logic */
            long compressed = compressStageSize(my_data->trace[j].size);
            long unsigned new_size = size_before;
            if (compressed > 0)
                new_size = (long unsigned)((double)my_data->trace[j].size * my_data->trace[j].size / (double)compressed);

            if (my_data->b_fs > 0.0)
                t_write = (double)new_size / my_data->b_fs;

            my_data->trace[j].service_time_c = dec_time + (float)(t_read + t_write);
            my_data->trace[j].size = new_size;
            st_sum += my_data->trace[j].service_time_c;
        }
        st_avg = st_sum / my_data->sizeWorker;

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage4.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

/**
 * @brief Input agent: decompress — restores the original size using inverse ratio.
 */
void decompress_time(struct worker *my_data)
{
    int j;
    char *command, *path;
    float st_sum, st_avg;

    st_sum = 0;
    st_avg = 0;
    path = getenv("PWD");

    if (my_data->sizeWorker > 0)
    {
        for (j = 0; j < my_data->sizeWorker; ++j)
        {
            double t_read = 0.0, t_write = 0.0;
            long unsigned size_before = my_data->trace[j].size;
            if (my_data->b_fs > 0.0)
                t_read = (double)size_before / my_data->b_fs;

            float dec_time = compressStage((double)size_before);
            /* Restore size: invert compression ratio using existing logic */
            long compressed = compressStageSize(my_data->trace[j].size);
            long unsigned new_size = size_before;
            if (compressed > 0)
                new_size = (long unsigned)((double)my_data->trace[j].size * my_data->trace[j].size / (double)compressed);

            if (my_data->b_fs > 0.0)
                t_write = (double)new_size / my_data->b_fs;

            my_data->trace[j].service_time_c = dec_time + (float)(t_read + t_write);
            my_data->trace[j].size = new_size;
            st_sum += my_data->trace[j].service_time_c;
        }
        st_avg = st_sum / my_data->sizeWorker;

        printf("Worker %d - Decompress stage: avg service time = %f seconds\n", my_data->id, st_avg);

        command = malloc(sizeof(char) * strlen(path) + 200);
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage1.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st_avg,
            my_data->sizeWorker,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

void upload_time(struct worker *my_data)
{
    int j;
    char *command, *path;

    path = getenv("PWD");
    for (j = 0; j < my_data->sizeWorker; ++j)
    {
        double t_write = 0.0;
        long unsigned size_out = my_data->trace[j].size;
        if (my_data->b_fs > 0.0)
            t_write = (double)size_out / my_data->b_fs;

        my_data->trace[j].service_time_io = (float)t_write;

        command = malloc(sizeof(char) * (strlen(path) + 256));
        sprintf(
            command,
            "docker exec %s%d ./single %f %f %d >> '%s/results/w%d_stage5.txt'",
            agent_container_prefix,
            my_data->id,
            my_data->trace[j].mean_interarrival,
            my_data->trace[j].service_time_io,
            my_data->trace[j].MUESTRAS,
            path,
            my_data->id);

        execute_command(command);
        free(command);
    }
}

/*command = malloc(strlen(getenv("PWD")) + 50 *sizeof(char));

strcpy(command, "");
sprintf( command,"mkdir -p %s/FilesCompress", getenv("PWD") );

fp = popen(command, "r");
if (fp == NULL) {
   printf("Failed to run command\n" );
   exit(1);
}
pclose(fp);

free(command);


command = malloc(strlen(getenv("PWD")) + 50 *sizeof(char));
strcpy(command, "");
sprintf( command,"chmod 777 -R %s/FilesCompress", getenv("PWD") );

fp = popen(command, "r");
if (fp == NULL) {
   printf("Failed to run command\n" );
   exit(1);
}
pclose(fp);
free(command);*/