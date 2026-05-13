/**
 * @file service_time.c
 * @mainpage Simulator to Preparation and retrieval service
 * @author Diana E. Carrizales-Espinoza
 * @date November 2019
 */

#include "service_time.h"
#include "proxy.h"
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

InterpolationPoint compress_table[MAX_INTERPOLATION_POINTS];
int compress_table_size = 0;

InterpolationPoint decompress_table[MAX_INTERPOLATION_POINTS];
int decompress_table_size = 0;

InterpolationPoint hashing_table[MAX_INTERPOLATION_POINTS];
int hashing_table_size = 0;

InterpolationPoint ida_table[MAX_INTERPOLATION_POINTS];
int ida_table_size = 0;

InterpolationPoint ida_decode_table[MAX_INTERPOLATION_POINTS];
int ida_decode_table_size = 0;

static char default_compression_algo[32] = "";
static char default_hashing_algo[32] = "";
static char default_ida_algo[32] = "";

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

void load_service_times(struct config *configuration)
{
    char line[512];
    FILE *fp;

    strncpy(default_compression_algo, configuration->compression_algo, sizeof(default_compression_algo) - 1);
    default_compression_algo[sizeof(default_compression_algo) - 1] = '\0';
    strncpy(default_hashing_algo, configuration->hashing_algo, sizeof(default_hashing_algo) - 1);
    default_hashing_algo[sizeof(default_hashing_algo) - 1] = '\0';
    strncpy(default_ida_algo, configuration->ida_algo, sizeof(default_ida_algo) - 1);
    default_ida_algo[sizeof(default_ida_algo) - 1] = '\0';

    // Load Cost-Efficiency (Compression)
    fp = fopen("real_values/cost-efficiency.csv", "r");
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

            if (algo && time_token && compress_table_size < MAX_INTERPOLATION_POINTS && decompress_table_size < MAX_INTERPOLATION_POINTS)
            {
                float size_mb = atof(size_token);
                float ratio = atof(ratio_str); // ignores 'x'
                float comp_s = atof(time_token);
                float decomp_s = decomp_token ? atof(decomp_token) : comp_s;

                strncpy(compress_table[compress_table_size].algo, algo, sizeof(compress_table[compress_table_size].algo) - 1);
                compress_table[compress_table_size].algo[sizeof(compress_table[compress_table_size].algo) - 1] = '\0';
                compress_table[compress_table_size].size = size_mb * 1048576.0f; // convert MB to bytes
                compress_table[compress_table_size].time = comp_s;
                compress_table[compress_table_size].ratio = ratio;
                compress_table_size++;

                strncpy(decompress_table[decompress_table_size].algo, algo, sizeof(decompress_table[decompress_table_size].algo) - 1);
                decompress_table[decompress_table_size].algo[sizeof(decompress_table[decompress_table_size].algo) - 1] = '\0';
                decompress_table[decompress_table_size].size = size_mb * 1048576.0f;
                decompress_table[decompress_table_size].time = decomp_s;
                decompress_table[decompress_table_size].ratio = ratio;
                decompress_table_size++;
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open real_values/cost-efficiency.csv\n");
    }

    // Load Integrity (Hashing)
    fp = fopen("real_values/integrity.csv", "r");
    if (fp)
    {
        fgets(line, sizeof(line), fp); // skip header
        while (fgets(line, sizeof(line), fp))
        {
            char *algo = trim_whitespace(strtok(line, ","));
            char *size_token = trim_whitespace(strtok(NULL, ","));
            strtok(NULL, ","); // num_objects
            char *time_token = trim_whitespace(strtok(NULL, ","));

            if (algo && time_token && hashing_table_size < MAX_INTERPOLATION_POINTS)
            {
                float size_mb = atof(size_token);
                float time_s = atof(time_token);

                strncpy(hashing_table[hashing_table_size].algo, algo, sizeof(hashing_table[hashing_table_size].algo) - 1);
                hashing_table[hashing_table_size].algo[sizeof(hashing_table[hashing_table_size].algo) - 1] = '\0';
                hashing_table[hashing_table_size].size = size_mb * 1048576.0f;
                hashing_table[hashing_table_size].time = time_s;
                hashing_table[hashing_table_size].ratio = 1.1f;
                hashing_table_size++;
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open real_values/integrity.csv\n");
    }

    // Load Reliability (IDA)
    fp = fopen("real_values/reliability.csv", "r");
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

                if (k == configuration->ida_k && m == configuration->ida_m && ida_table_size < MAX_INTERPOLATION_POINTS && ida_decode_table_size < MAX_INTERPOLATION_POINTS)
                {
                    strncpy(ida_table[ida_table_size].algo, algo, sizeof(ida_table[ida_table_size].algo) - 1);
                    ida_table[ida_table_size].algo[sizeof(ida_table[ida_table_size].algo) - 1] = '\0';
                    ida_table[ida_table_size].size = size_mb * 1048576.0f;
                    ida_table[ida_table_size].time = enc_s;
                    ida_table[ida_table_size].ratio = (float)(k + m) / k;
                    ida_table_size++;

                    strncpy(ida_decode_table[ida_decode_table_size].algo, algo, sizeof(ida_decode_table[ida_decode_table_size].algo) - 1);
                    ida_decode_table[ida_decode_table_size].algo[sizeof(ida_decode_table[ida_decode_table_size].algo) - 1] = '\0';
                    ida_decode_table[ida_decode_table_size].size = size_mb * 1048576.0f;
                    ida_decode_table[ida_decode_table_size].time = dec_s;
                    ida_decode_table[ida_decode_table_size].ratio = (float)(k + m) / k;
                    ida_decode_table_size++;
                }
            }
        }
        fclose(fp);
    }
    else
    {
        printf("Warning: Could not open real_values/reliability.csv\n");
    }
}

void print_interpolation_points()
{
    // Print the interpolation points for debugging
    for (int i = 0; i < compress_table_size; i++)
    {
        printf("Compress Table: %s %f %f %f\n", compress_table[i].algo, compress_table[i].size, compress_table[i].time, compress_table[i].ratio);
    }

    for (int i = 0; i < decompress_table_size; i++)
    {
        printf("Decompress Table: %s %f %f %f\n", decompress_table[i].algo, decompress_table[i].size, decompress_table[i].time, decompress_table[i].ratio);
    }

    for (int i = 0; i < hashing_table_size; i++)
    {
        printf("Hashing Table: %s %f %f %f\n", hashing_table[i].algo, hashing_table[i].size, hashing_table[i].time, hashing_table[i].ratio);
    }

    for (int i = 0; i < ida_table_size; i++)
    {
        printf("IDA Table: %s %f %f %f\n", ida_table[i].algo, ida_table[i].size, ida_table[i].time, ida_table[i].ratio);
    }

    for (int i = 0; i < ida_decode_table_size; i++)
    {
        printf("IDA Decode Table: %s %f %f %f\n", ida_decode_table[i].algo, ida_decode_table[i].size, ida_decode_table[i].time, ida_decode_table[i].ratio);
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
    return do_interpolate_algo((float)filesize, compress_table, compress_table_size, 0, algo && algo[0] ? algo : default_compression_algo);
}

float decompressStageAlgo(long unsigned filesize, const char *algo)
{
    return do_interpolate_algo((float)filesize, decompress_table, decompress_table_size, 0, algo && algo[0] ? algo : default_compression_algo);
}

double compressStageSize(double filesize)
{
    return compressStageSizeAlgo(filesize, default_compression_algo);
}

double compressStageSizeAlgo(double filesize, const char *algo)
{
    float ratio = do_interpolate_algo((float)filesize, compress_table, compress_table_size, 1, algo && algo[0] ? algo : default_compression_algo);
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
    return do_interpolate_algo((float)filesize, hashing_table, hashing_table_size, 0, algo && algo[0] ? algo : default_hashing_algo);
}

double hashingStageSize(double filesize)
{
    return hashingStageSizeAlgo(filesize, default_hashing_algo);
}

double hashingStageSizeAlgo(double filesize, const char *algo)
{
    float ratio = do_interpolate_algo((float)filesize, hashing_table, hashing_table_size, 1, algo && algo[0] ? algo : default_hashing_algo);
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
    return do_interpolate_algo((float)filesize, ida_table, ida_table_size, 0, algo && algo[0] ? algo : default_ida_algo);
}

float IDADecodeStageAlgo(double filesize, const char *algo)
{
    return do_interpolate_algo((float)filesize, ida_decode_table, ida_decode_table_size, 0, algo && algo[0] ? algo : default_ida_algo);
}

double IDAStageSize(double filesize)
{
    return IDAStageSizeAlgo(filesize, default_ida_algo);
}

double IDAStageSizeAlgo(double filesize, const char *algo)
{
    float ratio = do_interpolate_algo((float)filesize, ida_table, ida_table_size, 1, algo && algo[0] ? algo : default_ida_algo);
    if (ratio <= 0.0f)
        ratio = 1.0f;
    return (double)(filesize * ratio);
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
