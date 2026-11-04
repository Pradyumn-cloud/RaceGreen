# Synthetic fixture checkout

The Tier 1 records intentionally describe a small, generated checkout rather
than cloning a real project. A benchmark run materializes the checkout in its
own `.bench` worktree; this directory documents the stable relative location
used by the manifest. The generator is the source of truth for task identity,
control composition, and fixture contents. Do not edit generated fixture files
as benchmark inputs.
