# Gateway lab manifests

The smallest manifest repository that makes a Context Gateway serve the ETSI smoke suite: one
context space, one endpoint whose audience is `public`, and one policy granting the role `public`
the operations the suite performs. `../README.md` has the two `docker run` lines that use it.

The slug is fixed here so the recipe is copy-pasteable. A real endpoint's slug is random and
unguessable (EP-02); this repository never reaches a cluster.

`broker/` holds what the Portal's reconciler would write to the broker for this repository
(`seed.sh` writes it): the view of each Endpoint and the tenant's rule document. The reconciler
refuses the `smoke` Endpoint's view (its policy names no type, so the view would list every class
of the model, and the suite creates types the model does not have); the lab writes it by hand in
the space view's `*` form, as the space surface `/cs/{space}` has it. Regenerate the files when
`policies/` or `projections/` change.
