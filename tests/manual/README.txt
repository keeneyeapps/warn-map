Manual checks. Not part of run_all.py.

live_safeway.py   types "safeway" into the Destination slot on the LIVE site (needs network).
upgrade_sim.py    simulates an installed phone upgrading between two builds:
                  python tests/manual/upgrade_sim.py <old-build-dir> <new-build-dir>
                  (export a build with: git archive <commit> | tar -x -C <dir>)
