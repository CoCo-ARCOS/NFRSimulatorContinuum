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
    float size;
    float time;
    float ratio;
} InterpolationPoint;

InterpolationPoint compress_table[50];
int compress_table_size = 0;

InterpolationPoint hashing_table[50];
int hashing_table_size = 0;

InterpolationPoint ida_table[50];
int ida_table_size = 0;

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

            if (algo && strcmp(algo, configuration->compression_algo) == 0 && time_token)
            {
                float size_mb = atof(size_token);
                float ratio = atof(ratio_str); // ignores 'x'
                float comp_s = atof(time_token);

                compress_table[compress_table_size].size = size_mb * 1048576.0f; // convert MB to bytes
                compress_table[compress_table_size].time = comp_s;
                compress_table[compress_table_size].ratio = ratio;
                compress_table_size++;
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

            if (algo && strcmp(algo, configuration->hashing_algo) == 0 && time_token)
            {
                float size_mb = atof(size_token);
                float time_s = atof(time_token);

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

            if (algo && strcmp(algo, configuration->ida_algo) == 0 && time_token)
            {
                float size_mb = atof(size_token);
                float enc_s = atof(time_token);

                if (k == configuration->ida_k && m == configuration->ida_m)
                {
                    ida_table[ida_table_size].size = size_mb * 1048576.0f;
                    ida_table[ida_table_size].time = enc_s;
                    ida_table[ida_table_size].ratio = (float)(k + m) / k;
                    ida_table_size++;
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
        printf("Compress Table: %f %f %f\n", compress_table[i].size, compress_table[i].time, compress_table[i].ratio);
    }

    for (int i = 0; i < hashing_table_size; i++)
    {
        printf("Hashing Table: %f %f %f\n", hashing_table[i].size, hashing_table[i].time, hashing_table[i].ratio);
    }

    for (int i = 0; i < ida_table_size; i++)
    {
        printf("IDA Table: %f %f %f\n", ida_table[i].size, ida_table[i].time, ida_table[i].ratio);
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

float do_interpolate(float filesize, InterpolationPoint *table, int size, int use_ratio)
{
    if (size == 0)
        return 0.0f;
    if (filesize <= table[0].size)
    {
        return use_ratio ? table[0].ratio : table[0].time;
    }
    if (filesize >= table[size - 1].size)
    {
        return use_ratio ? table[size - 1].ratio : table[size - 1].time;
    }
    for (int y = 1; y < size; ++y)
    {
        if (filesize < table[y].size)
        {
            float y0 = use_ratio ? table[y].ratio : table[y].time;
            float y1 = use_ratio ? table[y - 1].ratio : table[y - 1].time;
            return interpolation(filesize, table[y].size, table[y - 1].size, y0, y1);
        }
    }
    return 0.0f;
}

float compressStage(long unsigned filesize)
{
    return do_interpolate((float)filesize, compress_table, compress_table_size, 0);
}

double compressStageSize(double filesize)
{
    float ratio = do_interpolate((float)filesize, compress_table, compress_table_size, 1);
    if (ratio <= 0.0f)
        ratio = 1.0f;
    return (double)(filesize / ratio);
}

float hashingStage(double filesize)
{
    return do_interpolate((float)filesize, hashing_table, hashing_table_size, 0);
}

double hashingStageSize(double filesize)
{
    float ratio = do_interpolate((float)filesize, hashing_table, hashing_table_size, 1);
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
    return do_interpolate((float)filesize, ida_table, ida_table_size, 0);
}

double IDAStageSize(double filesize)
{
    float ratio = do_interpolate((float)filesize, ida_table, ida_table_size, 1);
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