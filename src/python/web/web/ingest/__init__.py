"""Getting a book into the library: extract, resolve, review.

Two layers, and the split is the point of the whole design (docs/design.txt):

  * Extraction — `epub`/`m4b` read a file's own metadata and `pipeline` writes
    it to the raw tables verbatim, punctuation and inconsistencies intact. A
    wrong title in `epubs` is then a fact about the file, not a bug here.
  * Resolution — `resolve` maps one raw asset onto the abstract catalog, free
    and deterministic when title and authors match exactly (`candidates`), via
    one structured Claude call (`llm`) when they don't. `apply` executes the
    result; `summary` renders it for the admin who has to approve it.

This is the CLI tooling from tag cli-tooling-2026-07-21, moved in and stripped
of its argparse wrappers and batch drivers.
"""
