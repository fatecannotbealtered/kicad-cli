"""Developer entry; default is a read-only plan, never a product command/service."""

import argparse
import json

from . import support


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="Plan by default; explicit flags permit owned-copy execution")
    run.add_argument("--config", required=True)
    run.add_argument("--out", required=True)
    run.add_argument(
        "--case",
        choices=[
            *support.CASES,
            "refill-changed",
            "controller-selfcheck",
            "controller-resume-selfcheck",
            "controller-tree-selfcheck",
            "public-contract",
            "public-family",
            "bounded-concurrency",
            "environment-family",
            "candidate-diagnosis",
            "permission-selfcheck",
            "minimal-saved-recovery",
        ],
        required=True,
    )
    run.add_argument("--execute", action="store_true")
    run.add_argument("--allow-owned-termination", action="store_true")
    run.add_argument("--authorized-by")
    audit = sub.add_parser("audit", help="Read existing local witnesses; never replay writes")
    audit.add_argument("--evidence", required=True)
    audit.add_argument("--out", required=True)
    export = sub.add_parser("export", help="Publish an allowlisted summary of one existing case")
    export.add_argument("--case-dir", required=True)
    export.add_argument("--kind", choices=["fault", "refill"], required=True)
    export.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.action == "export":
        from .evidence import export_case

        print(json.dumps(export_case(args.case_dir, args.out, kind=args.kind)))
        return 0
    if args.action == "audit":
        from .evidence import export_previous

        export_previous(args.evidence, args.out)
        return 0
    plan = support.configure(
        args.config,
        args.out,
        execute=args.execute,
        control=args.allow_owned_termination,
        authorized_by=args.authorized_by,
    )
    plan["case"] = args.case
    if not args.execute:
        print(json.dumps(plan))
        return 0
    if (
        args.case.startswith("F")
        or args.case.startswith("controller-")
        or args.case == "bounded-concurrency"
        or args.case == "candidate-diagnosis"
    ) and not args.allow_owned_termination:
        parser.error("Fault execution additionally requires --allow-owned-termination")
    if args.case == "minimal-saved-recovery":
        from .minimal_saved import run

        result = run()
    elif args.case == "permission-selfcheck":
        from .environment_matrix import permission_selfcheck

        result = permission_selfcheck()
    elif args.case == "candidate-diagnosis":
        from .candidate_diagnosis import run

        result = run()
    elif args.case == "controller-tree-selfcheck":
        from .selfcheck import run_tree

        result = run_tree()
    elif args.case == "environment-family":
        from .environment_matrix import run

        result = run()
    elif args.case == "bounded-concurrency":
        from .concurrency import run

        result = run()
    elif args.case == "controller-resume-selfcheck":
        from .selfcheck import run_resume

        result = run_resume()
    elif args.case == "public-family":
        from .family_runner import run

        result = run()
    elif args.case == "public-contract":
        from .public_contract import run

        result = run()
    elif args.case == "controller-selfcheck":
        from .selfcheck import run

        result = run()
    elif args.case == "refill-changed":
        from .refill import run

        result = run()
    else:
        from .faults import run_case

        result = run_case(args.case)
    return 0 if result.get("status") in {"passed", "automated_passed_visual_pending"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
