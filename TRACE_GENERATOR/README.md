# Trace Generator

`trace_generator` es un componente escrito en C que permite generar trazas simuladas de llegada de datos para su uso en simulaciones de rendimiento y pruebas de estrés. Originalmente diseñado para simular sensores meteorológicos, este componente ha sido adaptado para generar datos genéricos, enfocándose únicamente en el **tiempo de llegada** (interarrival) y el **tamaño del objeto** simulado.

## Características

- Soporta tres distribuciones estadísticas para la generación de datos:
  - Uniforme
  - Poisson
  - Normal
- Permite configurar niveles de concurrencia y tamaño de buffer ajustables.
- Genera trazas por salida estándar (`stdout`) en un formato simple y fácil de parsear por otras herramientas: `<interarrival_time> <size>`.

## Compilación

El proyecto incluye un `Makefile` para facilitar la compilación. Simplemente ejecuta:

```bash
make clean
make
```

Esto compilará los archivos `.c` (incluyendo librerías genéricas y estadísticas) y generará el ejecutable `main`.

> [!NOTE]
> El `Makefile` incluye el flag `-fcommon` para el compilador GCC, necesario para el correcto enlazado de variables globales en versiones recientes de GCC (GCC 10+).

## Uso

Para ejecutar el generador, llama al ejecutable `main` y pásale todos los argumentos requeridos por línea de comandos:

```bash
./main <MUESTRAS> <inter_arrival> <DISTRIBUTION> <mean> <stddev> <SIZE> <stddevS> <Concurrency>
```

### Argumentos

1. **`MUESTRAS`**: Número total de muestras (líneas de traza) a generar.
2. **`inter_arrival`**: Tiempo base (o tasa) de inter-llegada entre objetos concurrentes.
3. **`DISTRIBUTION`**: Distribución estadística utilizada para modelar el tamaño del objeto. Valores aceptados:
   - `1`: Distribución Uniforme.
   - `2`: Distribución Poisson.
   - `3`: Distribución Normal.
4. **`mean`**: Media estadística (reservado/utilizado por funciones internas según el tipo de distribución).
5. **`stddev`**: Desviación estándar (reservado para la inter-llegada, según implementación interna).
6. **`SIZE`**: Tamaño medio base del objeto de datos a simular.
7. **`stddevS`**: Desviación estándar aplicada al tamaño del objeto.
8. **`Concurrency`**: Número de procesos simulados enviando datos al mismo tiempo.

### Ejemplo de Ejecución

Para generar una traza de `10000` muestras, con una inter-llegada de `5000.0`, usando distribución Normal (`3`), un tamaño medio de objeto de `15` con una desviación de tamaño de `0.6`, y una concurrencia de `1`:

```bash
./main 10000 5000.0 3 15 0.6 30 0.5 1 > trace_output.txt
```

> [!TIP]
> Se recomienda siempre redirigir la salida a un archivo (por ejemplo, `> trace.txt`), ya que las escrituras a `stdout` pueden ser muy extensas dependiendo de la cantidad de muestras (`MUESTRAS`).

## Salida de Datos

La salida estándar generada por la herramienta consiste en columnas delimitadas por espacios:

```
[Tiempo de inter-llegada] [Tamaño del objeto]
...
```

Ejemplo de la estructura de salida:
```
12345678 14
12350678 16
12355678 15
```
