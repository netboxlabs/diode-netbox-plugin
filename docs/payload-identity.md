# Payload identity

How the plugin decides whether two *payloads* describe the same object, before
anything has been looked up in the database.

This is the companion to [matching-criteria-documentation.md](./matching-criteria-documentation.md),
which is generated from the model constraints and lists the criteria per type.
That file answers *what can identify a `dcim.device`*. This one answers *what
the plugin is allowed to conclude from two payloads*, and — the part that has
caused every bug in this area — *when it is allowed to conclude it*.

## Two different questions

| | Question | Input | Where |
|---|---|---|---|
| **Row matching** | Does this payload match *this row*? | one payload, real rows | `matcher.py` (`find_existing_object`, `ObjectMatchCriteria.fingerprint`) |
| **Payload identity** | Can these *two payloads* be one object? | two payloads, no rows | `transformer.py` (`references_conflict`) |

They are not the same question and they do not have the same answers. A payload
that matches no row still has an identity; two payloads that match the same row
may still be incomparable to each other. Conflating them is how
`_carry_addressed_row` came to assert that two different primary keys "never
reach a merge" — true of the row-matching layer for one type, false of the
payload layer for every other.

## The answer is three-valued

`references_conflict` returns a bool, but it is computed over three states:

- **DIFFERENT** — these cannot be one object.
- **SAME** — these are one object.
- **UNKNOWN** — the payloads do not say.

UNKNOWN is not a failure mode, it is the common case, and collapsing it into
either neighbour has produced real bugs in both directions. Read as DIFFERENT
it invents refusals (a device named `RTR` on one side and `rtr` on the other
was reported as a bay differing from itself). Read as SAME it accepts
contradictions (a module installed in a bay other than the one carrying it,
applied `200` and left the real bay empty).

Only DIFFERENT refuses anything. UNKNOWN refuses nothing.

## Two asymmetric rules

**Difference needs one field.** Any identity field asserted on *both* sides
with different values proves two objects. This follows from a field being
single-valued, *not* from uniqueness: a row has one name, so two payloads
disagreeing about the name are two rows even where the name alone identifies
nothing.

**Sameness needs a whole constraint.** All fields of one unconditional unique
constraint, asserted on both sides and equal. This is where uniqueness does the
work: a unique constraint satisfied identically can only be one row.

**Sameness outranks difference.** Two references carrying one `asset_tag` are
one device even if they spell its name differently; that disagreement is a
field conflict on that row for `_merge_nodes` to report, not a second row.

**A conditional constraint never proves sameness.** `dcim_device_unique_name_site`
applies only where `tenant IS NULL`. A payload silent about `tenant` has not
said that, so its equality on `(name, site)` is not proof of one row. It still
contributes *difference* evidence through its fields.

## Precedence

1. **`metadata.source_match.netbox_id` on both sides.** An explicit primary key
   is the one identity statement here that needs no interpretation. Equal is
   one row, different is two, and it decides before every selector below.
2. **Any unconditional unique constraint fully asserted and equal** → SAME.
3. **Any identity field asserted on both sides and different** → DIFFERENT.
4. Otherwise UNKNOWN.

## Where the criteria come from

Read from `get_model_matchers(model_class)`, never restated. Every restatement
in this branch's history was incomplete — the bay name without its device, the
device without its site, the device without its `asset_tag`, the row without
its primary key — and each omission was found as a separate bug. Adding a type
or a constraint should be data, not code.

`ObjectMatchCriteria` supplies what the derivation needs: `_get_refs()` for the
field set (from `fields` or from `expressions`), `_get_insensitive_refs()` for
the fields a `Lower()` in the constraint makes case-insensitive, and
`condition` for the conditional case above.

Six matcher classes are hand-written and declare no field tuple. One of them
*is* extracted: `CustomFieldMatcher`, because it is built only from
`CustomField(unique=True)` — a genuine unique criterion, and for a producer
keying devices by an external id it may be the only selector a reference
carries. It is read as a custom-field name rather than a field tuple.

The other five are skipped, and not merely for want of a field list:

- `GlobalIPNetworkIPMatcher`, `VRFIPNetworkIPMatcher` — compare addresses,
  where two spellings of one address (`::1` and `0:0:0:0:0:0:0:1`) are equal
  values and unequal strings. A textual verdict would be wrong in the refusing
  direction.
- `CableTerminationSetMatcher` — compares a *set* of terminations.
- `VirtualChassisNameMatcher` — matches a name carrying no uniqueness at all;
  deriving identity from it is exactly what the VC partition exists to avoid.
- `AutoSlugMatcher` — costs nothing, the plain `unique_slug` criterion already
  covers that field.

A skipped matcher costs evidence; it cannot invent it.

Reference fields recurse into the referenced type's own identity — a bay's
device, that device's site — so `site: {"name": "s"}` versus
`site: {"slug": "s"}` is UNKNOWN rather than a text mismatch. Depth is bounded
as a backstop against a reference cycle, not as a semantic choice.

## What is not decidable here

**Disjoint selectors.** Two references naming an object through criteria that
do not overlap — an `asset_tag` on one side, a `name` on the other — share
nothing to compare and are UNKNOWN, whatever the database would say.

This is a real limit, not a rounding error, and it is load-bearing: an earlier
revision *documented* it and that documentation hid a non-converging success
across several review rounds. So where the residue makes a wrong write
reachable, the caller hydrates the addressed side and compares fully specified
payloads (`_hydrate_addressed_sides`, `_describe_row`) rather than this relation
guessing. A lookup is a legitimate answer; a guess is not.

**The backstop is a second check, after resolution.**
`_check_reverse_side_resolves_to_its_parent` runs after
`_resolve_existing_references`, where every way of expressing identity has
already collapsed to a primary key or a node this change set creates. It
compares those, so no selector, spelling or criterion can be incomplete about
it — including the disjoint-selector case above, which no payload comparison
can settle.

That is the division of labour, and it is why the payload check is *allowed* to
be approximate: it exists for its **message**, refusing in the producer's own
vocabulary before any lookup, while coverage is the post-resolution pass's job.
A payload the first check cannot compare is still refused, just less specifically.
If you find yourself extending the payload comparison to reach a new case, ask
first whether the backstop already covers it — it probably does, and the
question is only whether the message is good enough.

**The hydration trigger is the verdict, not a field.** Hydrate when the payload
comparison returned UNKNOWN and a side says which row it is. An earlier
revision gated it on a side merely *carrying a name*, which went stale against
the criteria immediately — `ModuleBay` is identified by `(name, device)`, so an
addressed bay that named itself but omitted its device was treated as
comparable, and a same-named bay on another device compared as compatible.
Asking whether the comparison actually reached a verdict cannot go stale that
way, and it means ordinary payloads pay no lookup at all.

**Error messages are derived from the same criteria.** Whatever the comparison
used to decide is what the message shows. Hand-written, that description gained
the bay name, then the device name, then the site, then the `asset_tag`, each
added only after a payload turned up that it described *identically on both
sides* — `module_bay is 'case-bay', not 'case-bay'`. A unique custom field
would have been the next one.

If a check finds itself needing resolved identity for *both* sides, it belongs
after `_resolve_existing_references`, not here — see the staging table.

## What each pipeline stage knows

| Stage | Identity available |
|---|---|
| `transform_proto_json` (entity body) | payload selectors only; nested payloads not yet snake-cased; `metadata` already popped from the entity |
| `_topo_sort` | as above, plus reference edges |
| `_fingerprint_dedupe` | payload selectors; `_netbox_id` present but only *qualifying* fingerprints for a contested VC master |
| `_resolve_existing_references` | real primary keys, for the first time |
| after `_handle_post_creates` | primary keys, or a node this change set creates — where `_check_reverse_side_resolves_to_its_parent` runs |
| differ / applier | rows |

Two consequences worth keeping in mind. `_ensure_snake_case` is **shallow** — it
rewrites the keys of the object being transformed, and each nested object is
normalised later by its own recursion — so anything reading a nested payload
must tolerate `moduleBay` as well as `module_bay` (`_asserted`). And an
entity's `metadata` is popped *before* the transform body runs, so a check that
needs the addressed row must be handed it explicitly.

## Why `dcim.virtualchassis` has its own machinery

`VirtualChassis` has no unique constraint on `name`. The derived relation
therefore concludes nothing from two chassis names — correctly, and this is
pinned by a test, because the alternative is inventing identity the model does
not have. Everything the plugin needs to tell two same-named chassis apart is
consequently explicit and separate: `asserted_vc_identity`,
`vc_identities_conflict` (compatibility, not equality), `_VC_DISCRIMINATORS`,
and `partition_vc_identities`.

That machinery is deliberately *not* folded into `references_conflict`. It
answers a narrower question — given a name bucket, how many chassis are there —
and it is the only place where `master`, a field that is identity but cannot be
filtered on at transform time, participates.

Related: `dcim.virtualchassis` is the only member of
`matcher._PRE_SAVE_MATCH_BIND_ONLY`, for the same underlying reason — a name
match carrying no DB uniqueness may have found a different stack, so a CREATE
binds to it without writing.

## History, kept on purpose

Five consecutive review findings in this area were the same shape: a
hand-written comparison that knew some selectors and not others. In order —
the bay name, then the device's site and tenant, then `asset_tag`, then
`metadata.source_match.netbox_id`, then a bay addressed by primary key alone.
Each fix was correct and none made the next less likely, because nothing in
the code stated what the complete set was. That is what this document and the
derivation are for.

Two more arrived *after* the derivation, and both were in what remained
hand-written **around** it rather than in the derived comparison: the hydration
gate (a field-presence test instead of a verdict test) and the blanket skip of
hand-written matchers (which silently dropped unique custom fields). Both are
now derived too. The lesson generalises past this file: a derived core with
hand-written seams fails at the seams, so when adding one, ask what it restates
that the criteria already say.
