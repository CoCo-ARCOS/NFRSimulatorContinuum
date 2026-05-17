/**
 * @file service_time.c
 * @mainpage Simulator to Preparation and retrieval service
 * @author Diana E. Carrizales-Espinoza
 * @date November 2019
 */

#include "service_time.h"
#include "proxy.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct
{
    char algo[32];
    float size;
    float time;
    float ratio;
} InterpolationPoint;

#define MAX_INTERPOLATION_POINTS 256

typedef struct
{
    char name[64];
    char source_dir[PATH_MAX];
    InterpolationPoint compress_table[MAX_INTERPOLATION_POINTS];
    int compress_table_size;
    InterpolationPoint decompress_table[MAX_INTERPOLATION_POINTS];
    int decompress_table_size;
    InterpolationPoint hashing_table[MAX_INTERPOLATION_POINTS];
    int hashing_table_size;
    InterpolationPoint ida_table[MAX_INTERPOLATION_POINTS];
    int ida_table_size;
    InterpolationPoint ida_decode_table[MAX_INTERPOLATION_POINTS];
    int ida_decode_table_size;
} ServiceProfile;

static ServiceProfile service_profiles[MAX_MACHINES + 1];
static int service_profiles_count = 0;
static ServiceProfile *default_service_profile = NULL;
static __thread ServiceProfile *active_service_profile = NULL;

static char default_compression_algo[32] = "";
static char default_hashing_algo[32] = "";
static char default_ida_algo[32] = "";

static ServiceProfile *current_service_profile(void)
{
    if (active_service_profile)
        return active_service_profile;
    return default_service_profile;
}

static void reset_service_profile(ServiceProfile *profile)
{
    if (!profile)
        return;
    memset(profile, 0, sizeof(*profile));
}

static void build_path(char *buffer, size_t buffer_size, const char *dir, const char *file_name)
{
    if (!buffer || buffer_size == 0)
        return;

    if (!dir || dir[0] == '\0')
    {
        snprintf(buffer, buffer_size, "%s", file_name ? file_name : "");
        return;
    }

    if (!file_name || file_name[0] == '\0')
    {
        snprintf(buffer, buffer_size, "%s", dir);
        return;
    }

    snprintf(buffer, buffer_size, "%s/%s", dir, file_name);
}

static void resolve_path(char *buffer, size_t buffer_size, const char *path, const char *base_dir)
{
    if (!buffer || buffer_size == 0)
        return;

    if (!path || path[0] == '\0')
    {
        buffer[0] = '\0';
        return;
    }

    if (path[0] == '/')
    {
        snprintf(buffer, buffer_size, "%s", path);
        return;
    }

    if (base_dir && base_dir[0] != '\0')
        snprintf(buffer, buffer_size, "%s/%s", base_dir, path);
    else
        snprintf(buffer, buffer_size, "%s", path);
}

static char *trim_whitespace(char *str)
{
    char *end;
    if (!str) return str;
    while (*str == ' ' || *str == '\t' || *str == '\r' || *str == '\n') str++;
    if (*str == '\0') return str;
    end = str + strlen(str) - 1;
    while (end > str && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')) {
        *end = '\0';
        end--;
    }
    return str;
}

static int load_profile_from_dir(ServiceProfile *profile, const struct config *configuration, const char *directory)
{
    char line[512];
    FILE *fp;
    char csv_path[PATH_MAX];

    if (!profile || !configuration || !directory || directory[0] == '\0')
        return -1;

    reset_service_profile(profile);
    strncpy(profile->source_dir, directory, sizeof(profile->source_dir) - 1);
    profile->source_dir[sizeof(profile->source_dir) - 1] = '\0';

    // Load Cost-Efficiency (Compression)
    build_path(csv_path, sizeof(csv_path), directory, "cost-efficiency.csv");
    fp = fopen(csv_path, "r");
    if (fp)
    {
        fgets(line, sizeof(line), fp); // skip header
        while (fgets(line, sizeof(line), fp))
        {
            char *algo = trim_whitespace(strtok(line, ","));
            char *size_token = trim_whitespace(strtok(NULL, ","));
            strtok(NULL, ","); // num_objects
            char *ratio_str = trim_whitespace(strtok(NULL, ","));
            char *time_token = trim_whitespace(strtok(NULL, ","));
            strtok(NULL, ","); // std_comp_s
            strtok(NULL, ","); // avg_io_write_s
            strtok(NULL, ","); // std_io_write_s
            strtok(NULL, ","); // avg_io_read_s
            strtok(NULL, ","); // std_io_read_s
            char *decomp_token = trim_whitespace(strtok(NULL, ","));

            if (algo && time_token && profile->compress_table_size < MAX_INTERPOLATION_POINTS && profile->decompress_table_size < MAX_INTERPOLATION_POINTS)
            {
                float size_mb = atof(size_token);
                float ratio = atof(ratio_str); // ignores 'x'
                float comp_s = atof(time_token);
                float decomp_s = decomp_token ? atof(decomp_token) : comp_s;

                strncpy(profile->compress_table[profile->compress_table_size].algo, algo, sizeof(profile->compress_table[profile->compress_table_size].algo) - 1);
                profile->compress_table[profile->compress_table_size].algo[sizeof(profile->compress_table[profile->compress_table_size].algo) - 1] = '\0';
                profile->compress_table[profile->compress_table_size].size = size_mb * 1048576.0f; // convert MB to bytes
                profile->compress_table[profile->compress_table_size].time = comp_s;
                profile->compress_table[profile->compress_table_size].ratio = ratio;
                profile->compress_table_size++;

                strncpy(profile->decompress_table[profile->decompress_table_size].algo, algo, sizeof(profile->decompress_table[profile->decompress_table_size].algo) - 1);
                profile->decompress_table[profile->decompress_table_size].algo[sizeof(profile->decompress_table[profile->decompress_table_size].algo) - 1] = '\0';
                profile->decompress_table[profile->decompress_table_size].size = size_mb * 1048576.0f;
                profile->decompress_table[profile->decompress_table_size].time = decomp_s;
                profile->decompress_table[profile->decompress_table_size].ratio = ratio;
                profile->decompress_table_size++;
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open %s\n", csv_path);
    }

    // Load Integrity (Hashing)
    build_path(csv_path, sizeof(csv_path), directory, "integrity.csv");
    fp = fopen(csv_path, "r");
    if (fp)
    {
        fgets(line, sizeof(line), fp); // skip header
        while (fgets(line, sizeof(line), fp))
        {
            char *algo = trim_whitespace(strtok(line, ","));
            char *size_token = trim_whitespace(strtok(NULL, ","));
            strtok(NULL, ","); // num_objects
            char *time_token = trim_whitespace(strtok(NULL, ","));

            if (algo && time_token && profile->hashing_table_size < MAX_INTERPOLATION_POINTS)
            {
                float size_mb = atof(size_token);
                float time_s = atof(time_token);

                strncpy(profile->hashing_table[profile->hashing_table_size].algo, algo, sizeof(profile->hashing_table[profile->hashing_table_size].algo) - 1);
                profile->hashing_table[profile->hashing_table_size].algo[sizeof(profile->hashing_table[profile->hashing_table_size].algo) - 1] = '\0';
                profile->hashing_table[profile->hashing_table_size].size = size_mb * 1048576.0f;
                profile->hashing_table[profile->hashing_table_size].time = time_s;
                profile->hashing_table[profile->hashing_table_size].ratio = 1.1f;
                profile->hashing_table_size++;
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open %s\n", csv_path);
    }

    // Load Reliability (IDA)
    build_path(csv_path, sizeof(csv_path), directory, "reliability.csv");
    fp = fopen(csv_path, "r");
    if (fp)
    {
        fgets(line, sizeof(line), fp); // skip header
        while (fgets(line, sizeof(line), fp))
        {
            char *algo = trim_whitespace(strtok(line, ","));
            char *size_token = trim_whitespace(strtok(NULL, ","));
            int k = atoi(trim_whitespace(strtok(NULL, ","))); // k_datos
            int m = atoi(trim_whitespace(strtok(NULL, ","))); // m_paridad
            strtok(NULL, ",");               // num_objects
            char *time_token = trim_whitespace(strtok(NULL, ","));
            strtok(NULL, ","); // std_encoding_s
            char *decode_token = trim_whitespace(strtok(NULL, ","));

            if (algo && strcmp(algo, configuration->ida_algo) == 0 && time_token)
            {
                float size_mb = atof(size_token);
                float enc_s = atof(time_token);
                float dec_s = decode_token ? atof(decode_token) : enc_s;

                if (k == configuration->ida_k && m == configuration->ida_m && profile->ida_table_size < MAX_INTERPOLATION_POINTS && profile->ida_decode_table_size < MAX_INTERPOLATION_POINTS)
                {
                    strncpy(profile->ida_table[profile->ida_table_size].algo, algo, sizeof(profile->ida_table[profile->ida_table_size].algo) - 1);
                    profile->ida_table[profile->ida_table_size].algo[sizeof(profile->ida_table[profile->ida_table_size].algo) - 1] = '\0';
                    profile->ida_table[profile->ida_table_size].size = size_mb * 1048576.0f;
                    profile->ida_table[profile->ida_table_size].time = enc_s;
                    profile->ida_table[profile->ida_table_size].ratio = (float)(k + m) / k;
                    profile->ida_table_size++;

                    strncpy(profile->ida_decode_table[profile->ida_decode_table_size].algo, algo, sizeof(profile->ida_decode_table[profile->ida_decode_table_size].algo) - 1);
                    profile->ida_decode_table[profile->ida_decode_table_size].algo[sizeof(profile->ida_decode_table[profile->ida_decode_table_size].algo) - 1] = '\0';
                    profile->ida_decode_table[profile->ida_decode_table_size].size = size_mb * 1048576.0f;
                    profile->ida_decode_table[profile->ida_decode_table_size].time = dec_s;
                    profile->ida_decode_table[profile->ida_decode_table_size].ratio = (float)(k + m) / k;
                    profile->ida_decode_table_size++;
                }
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open %s\n", csv_path);
    }

    if (profile->compress_table_size == 0 && profile->hashing_table_size == 0 && profile->ida_table_size == 0)
        return -1;

    return 0;
}

void load_service_times(struct config *configuration)
{
    load_service_times_with_base(configuration, ".");
}

void load_service_times_with_base(struct config *configuration, const char *runtime_base_dir)
{
    char default_dir[PATH_MAX];

    if (!configuration)
        return;

    service_profiles_count = 0;
    default_service_profile = NULL;
    active_service_profile = NULL;

    strncpy(default_compression_algo, configuration->compression_algo, sizeof(default_compression_algo) - 1);
    default_compression_algo[sizeof(default_compression_algo) - 1] = '\0';
    strncpy(default_hashing_algo, configuration->hashing_algo, sizeof(default_hashing_algo) - 1);
    default_hashing_algo[sizeof(default_hashing_algo) - 1] = '\0';
    strncpy(default_ida_algo, configuration->ida_algo, sizeof(default_ida_algo) - 1);
    default_ida_algo[sizeof(default_ida_algo) - 1] = '\0';

    if (configuration->real_values_dir[0] != '\0')
        resolve_path(default_dir, sizeof(default_dir), configuration->real_values_dir, runtime_base_dir);
    else
        build_path(default_dir, sizeof(default_dir), runtime_base_dir, "real_values");

    default_service_profile = &service_profiles[service_profiles_count];
    strncpy(default_service_profile->name, "default", sizeof(default_service_profile->name) - 1);
    default_service_profile->name[sizeof(default_service_profile->name) - 1] = '\0';
    load_profile_from_dir(default_service_profile, configuration, default_dir);
    service_profiles_count++;

    for (int mid = 0; mid < configuration->machines_number && service_profiles_count < MAX_MACHINES + 1; ++mid)
    {
        char machine_dir[PATH_MAX];
        struct machine_node *machine = &configuration->machines[mid];

        machine->service_profile_index = 0;
        if (machine->real_values_dir[0] != '\0')
            resolve_path(machine_dir, sizeof(machine_dir), machine->real_values_dir, runtime_base_dir);
        else if (machine->hardware_profile[0] != '\0')
        {
            char profile_root[PATH_MAX];
            char profile_dir[PATH_MAX];
            build_path(profile_root, sizeof(profile_root), runtime_base_dir, "results_different_machines/organized");
            build_path(profile_dir, sizeof(profile_dir), profile_root, machine->hardware_profile);
            build_path(machine_dir, sizeof(machine_dir), profile_dir, "real_values");
        }
        else
            machine_dir[0] = '\0';

        if (machine_dir[0] == '\0')
            continue;

        ServiceProfile *profile = &service_profiles[service_profiles_count];
        strncpy(profile->name, machine->hardware_profile[0] ? machine->hardware_profile : machine->name, sizeof(profile->name) - 1);
        profile->name[sizeof(profile->name) - 1] = '\0';
        if (load_profile_from_dir(profile, configuration, machine_dir) == 0)
        {
            machine->service_profile_index = service_profiles_count;
            service_profiles_count++;
        }
    }
}

void print_interpolation_points()
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return;

    // Print the interpolation points for debugging
    for (int i = 0; i < profile->compress_table_size; i++)
    {
        printf("Compress Table: %s %f %f %f\n", profile->compress_table[i].algo, profile->compress_table[i].size, profile->compress_table[i].time, profile->compress_table[i].ratio);
    }

    for (int i = 0; i < profile->decompress_table_size; i++)
    {
        printf("Decompress Table: %s %f %f %f\n", profile->decompress_table[i].algo, profile->decompress_table[i].size, profile->decompress_table[i].time, profile->decompress_table[i].ratio);
    }

    for (int i = 0; i < profile->hashing_table_size; i++)
    {
        printf("Hashing Table: %s %f %f %f\n", profile->hashing_table[i].algo, profile->hashing_table[i].size, profile->hashing_table[i].time, profile->hashing_table[i].ratio);
    }

    for (int i = 0; i < profile->ida_table_size; i++)
    {
        printf("IDA Table: %s %f %f %f\n", profile->ida_table[i].algo, profile->ida_table[i].size, profile->ida_table[i].time, profile->ida_table[i].ratio);
    }

    for (int i = 0; i < profile->ida_decode_table_size; i++)
    {
        printf("IDA Decode Table: %s %f %f %f\n", profile->ida_decode_table[i].algo, profile->ida_decode_table[i].size, profile->ida_decode_table[i].time, profile->ida_decode_table[i].ratio);
    }
}

float interpolation(float x, float x0, float x1, float y0, float y1)
{
    float y = 0;
    if (x0 == x1)
        return y0;
    y = (float)((x - x1) / (x0 - x1)) * (y0 - y1) + y1;
    return y;
}

static int algo_matches(const char *requested, const char *candidate)
{
    if (!requested || requested[0] == '\0')
        return 1;
    return candidate && strcmp(candidate, requested) == 0;
}

float do_interpolate_algo(float filesize, InterpolationPoint *table, int size, int use_ratio, const char *algo)
{
    int previous = -1;
    if (size == 0)
        return 0.0f;

    for (int y = 0; y < size; ++y)
    {
        if (!algo_matches(algo, table[y].algo))
            continue;

        if (filesize <= table[y].size)
        {
            float y0 = use_ratio ? table[y].ratio : table[y].time;
            if (previous < 0)
                return y0;

            float y1 = use_ratio ? table[previous].ratio : table[previous].time;
            return interpolation(filesize, table[y].size, table[previous].size, y0, y1);
        }

        previous = y;
    }

    if (previous >= 0)
        return use_ratio ? table[previous].ratio : table[previous].time;

    return 0.0f;
}

float do_interpolate(float filesize, InterpolationPoint *table, int size, int use_ratio)
{
    return do_interpolate_algo(filesize, table, size, use_ratio, NULL);
}

float compressStage(long unsigned filesize)
{
    return compressStageAlgo(filesize, default_compression_algo);
}

float decompressStage(long unsigned filesize)
{
    return decompressStageAlgo(filesize, default_compression_algo);
}

float compressStageAlgo(long unsigned filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return 0.0f;
    return do_interpolate_algo((float)filesize, profile->compress_table, profile->compress_table_size, 0, algo && algo[0] ? algo : default_compression_algo);
}

float decompressStageAlgo(long unsigned filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return 0.0f;
    return do_interpolate_algo((float)filesize, profile->decompress_table, profile->decompress_table_size, 0, algo && algo[0] ? algo : default_compression_algo);
}

double compressStageSize(double filesize)
{
    return compressStageSizeAlgo(filesize, default_compression_algo);
}

double compressStageSizeAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    float ratio;
    if (!profile)
        return filesize;
    ratio = do_interpolate_algo((float)filesize, profile->compress_table, profile->compress_table_size, 1, algo && algo[0] ? algo : default_compression_algo);
    if (ratio <= 0.0f)
        ratio = 1.0f;
    printf("CompressStageSizeAlgo: filesize = %f, ratio = %f\n", filesize, ratio);
    return (double)(filesize / ratio);
}

float hashingStage(double filesize)
{
    return hashingStageAlgo(filesize, default_hashing_algo);
}

float hashingStageAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return 0.0f;
    return do_interpolate_algo((float)filesize, profile->hashing_table, profile->hashing_table_size, 0, algo && algo[0] ? algo : default_hashing_algo);
}

double hashingStageSize(double filesize)
{
    return hashingStageSizeAlgo(filesize, default_hashing_algo);
}

double hashingStageSizeAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    float ratio;
    if (!profile)
        return filesize;
    ratio = do_interpolate_algo((float)filesize, profile->hashing_table, profile->hashing_table_size, 1, algo && algo[0] ? algo : default_hashing_algo);
    if (ratio <= 0.0f)
        ratio = 1.0f;
    return (double)(filesize * ratio);
}

float indexingStage(long numFiles)
{
    float tested[10][2] = {
        {1, 296},
        {10, 375},
        {100, 29584},
        {200, 30405},
        {500, 35637},
        {1000, 38197},
        {2000, 40456},
        {4000, 43457},
        {6000, 44567},
        {10000, 108946}};

    if (numFiles <= tested[0][0])
        return tested[0][1] / 1000.0f;
    if (numFiles >= tested[9][0])
        return tested[9][1] / 1000.0f;
    for (int y = 1; y < 10; ++y)
    {
        if (numFiles < tested[y][0])
        {
            /* interpolation returns value in same units as table (ms), convert to seconds */
            float ms = interpolation(numFiles, tested[y][0], tested[y - 1][0], tested[y][1], tested[y - 1][1]);
            return ms / 1000.0f;
        }
    }
    return 0.0f;
}

float IDAStage(double filesize)
{
    return IDAStageAlgo(filesize, default_ida_algo);
}

float IDADecodeStage(double filesize)
{
    return IDADecodeStageAlgo(filesize, default_ida_algo);
}

float IDAStageAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return 0.0f;
    return do_interpolate_algo((float)filesize, profile->ida_table, profile->ida_table_size, 0, algo && algo[0] ? algo : default_ida_algo);
}

float IDADecodeStageAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    if (!profile)
        return 0.0f;
    return do_interpolate_algo((float)filesize, profile->ida_decode_table, profile->ida_decode_table_size, 0, algo && algo[0] ? algo : default_ida_algo);
}

double IDAStageSize(double filesize)
{
    return IDAStageSizeAlgo(filesize, default_ida_algo);
}

double IDAStageSizeAlgo(double filesize, const char *algo)
{
    ServiceProfile *profile = current_service_profile();
    float ratio;
    if (!profile)
        return filesize;
    ratio = do_interpolate_algo((float)filesize, profile->ida_table, profile->ida_table_size, 1, algo && algo[0] ? algo : default_ida_algo);
    if (ratio <= 0.0f)
        ratio = 1.0f;
    return (double)(filesize * ratio);
}

void set_service_time_profile(int profile_index)
{
    if (profile_index >= 0 && profile_index < service_profiles_count)
        active_service_profile = &service_profiles[profile_index];
    else
        active_service_profile = default_service_profile;
}

/*float uploadStage (long  unsigned filesize) {
  float tested[7][2]={
     { 1048576, 12568 } ,
     { 33554432, 236581 } ,
     { 67108864, 418350 } ,
     { 134217728, 806219 } ,
     { 268435456, 1545408 } ,
     { 536870912, 3048114 } ,
     { 1073741824, 6055735 } ,
    };

    if (filesize <= tested[0][0]) return tested[0][1];
    if (filesize >= tested[6][0]) return tested[6][1];
    for (int y = 1; y < 7; ++y) {
        if (filesize < tested[y][0]) {
            return interpolation(filesize, tested[y][0], tested[y-1][0], tested[y][1], tested[y-1][1]);
        }
    }
    return 0;
}*/
