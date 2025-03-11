

### Virtual Container Image construction

First, you have to build two virtual container images: 

```bash
docker build -t single:queue ./stages
docker build -t trace:generator ./TRACE_GENERATOR
```

### Simulator execution

Go to the ```proxy_dd``` directory, compile the code, and execute the main program:

```bash
cd proxy_dd
make
./main
```

#### Configurations

In ```proxy_dd``` directory, edit the file ```config.cfg``` specifying the following:

* ```workers```: number of parallel workers to simulate.
* ```traces_number```: number of input traces for each worker.
* ```traces_fileName```: configuration file containing the parameters of each trace.

