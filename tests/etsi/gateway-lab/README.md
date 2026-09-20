# Gateway lab manifests

The smallest manifest repository that makes a Context Gateway serve the ETSI smoke suite: one
context space, one endpoint whose audience is `public`, and one policy granting the role `public`
the operations the suite performs. `../README.md` has the two `docker run` lines that use it.

The slug is fixed here so the recipe is copy-pasteable. A real endpoint's slug is random and
unguessable (EP-02); this repository never reaches a cluster.
