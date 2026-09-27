# Kosha console

Fixture-driven dashboard: Live session, Approval queue, Benchmarks. React + Vite, no
backend. Tabs are linkable as `#live`, `#queue`, `#bench`.

```sh
cd bench/web
npm install
npm run fixtures   # rebuild src/fixtures/*.json from bench/results, config/ and StepShield
npm run dev        # or: npm run build && npm run preview
```

`export_fixtures.py` copies the committed bench summaries, the M1 price table and
effects.yaml, and rebuilds four Bench B composed fleets with `bench/compose_fleet.py`'s
own seeds so every step carries its full `policy.decide` Decision. It aborts if a
rebuilt fleet's members or first flag differ from `bench/results/compose_fleet.json`.
Approval buttons record resolutions in the browser only; no koshad is attached.
