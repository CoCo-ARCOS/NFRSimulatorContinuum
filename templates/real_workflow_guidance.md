# Adding a real workflow

Add a new object to the `workflows` array in a copy of `configs/workflows.json`. It must contain:

- a unique `id`;
- `tasks`, each with `id`, measured or trace-derived `service_time_s`, and `output_size_factor`;
- `edges`, each with `id`, `from`, `to`, `policy_group`, `risk_tags`, and `nfr_families`;
- a `plans` array containing WMS-generated task-to-machine placements.

Use artifact classes already recognized by the default contract rules:

- `sensitive` for disclosure clauses;
- `critical` for corruption-detection clauses;
- `authentic` for authenticated-modification clauses;
- `resilient` for fragment-loss clauses;
- `bulk` for data-reduction clauses.

The task times, artifact factors, arrivals, and plan placements must come from the workflow traces or measurements. The evaluation scripts intentionally do not invent these values.
