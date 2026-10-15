import sys

if sys.argv[1:2] == ["trace"]:
    from minicodex.backend.cli import trace_main

    raise SystemExit(trace_main(sys.argv[2:]))
else:
    from minicodex.frontend.desktop import main

    raise SystemExit(main())
