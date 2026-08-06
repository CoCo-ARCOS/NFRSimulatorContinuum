#!/usr/bin/env nextflow

/*
 * Nextflow adapter for contract-driven NFR realization.
 *
 * Nextflow runs tasks as external processes, so it consumes the realization
 * plan through the nfr_apply.py file interface rather than importing the
 * Python API. Every protected artefact crossing a workflow edge is passed
 * through the same enforcement path used by the in-process engines, and each
 * invocation appends to the shared enforcement log.
 */

nextflow.enable.dsl = 2

params.plan = null
params.apply = null
params.mixer = null
// Nextflow tasks run in a fresh shell that has not activated our virtualenv,
// so the interpreter is passed in explicitly rather than resolved from PATH.
params.python = 'python3'
params.payload_bytes = 16777216
params.outdir = 'results'
params.log = null

process INGEST {
    output:
    path 'raw.bin'

    script:
    """
    head -c ${params.payload_bytes} /dev/urandom > raw.bin
    """
}

process PROTECT_RAW {
    input:
    path payload

    output:
    tuple path('raw.nfr'), path('raw.steps.json')

    script:
    """
    ${params.python} ${params.apply} \
        --plan ${params.plan} \
        --artifact-class raw \
        --direction output \
        --in ${payload} \
        --out raw.nfr \
        --steps raw.steps.json \
        --log ${params.log} \
        --stage protect:raw-data
    """
}

process UNPROTECT_RAW {
    input:
    tuple path(payload), path(steps)

    output:
    path 'raw.restored'

    script:
    """
    ${params.python} ${params.apply} \
        --plan ${params.plan} \
        --artifact-class raw \
        --direction input \
        --in ${payload} \
        --out raw.restored \
        --steps ${steps} \
        --log ${params.log} \
        --stage unprotect:raw-data
    """
}

process COMPUTE {
    input:
    path payload

    output:
    path 'derived.bin'

    script:
    """
    ${params.python} ${params.mixer} ${payload} derived.bin
    """
}

process PROTECT_DERIVED {
    input:
    path payload

    output:
    tuple path('derived.nfr'), path('derived.steps.json')

    script:
    """
    ${params.python} ${params.apply} \
        --plan ${params.plan} \
        --artifact-class derived \
        --direction output \
        --in ${payload} \
        --out derived.nfr \
        --steps derived.steps.json \
        --log ${params.log} \
        --stage protect:derived-data
    """
}

process UNPROTECT_DERIVED {
    publishDir params.outdir, mode: 'copy'

    input:
    tuple path(payload), path(steps)

    output:
    path 'derived.restored'

    script:
    """
    ${params.python} ${params.apply} \
        --plan ${params.plan} \
        --artifact-class derived \
        --direction input \
        --in ${payload} \
        --out derived.restored \
        --steps ${steps} \
        --log ${params.log} \
        --stage unprotect:derived-data
    """
}

workflow {
    if (!params.plan || !params.apply || !params.mixer) {
        error '--plan, --apply and --mixer are all required'
    }
    INGEST()
        | PROTECT_RAW
        | UNPROTECT_RAW
        | COMPUTE
        | PROTECT_DERIVED
        | UNPROTECT_DERIVED
}
