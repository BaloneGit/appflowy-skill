from __future__ import annotations

import argparse

from _common import print_json
from appflowy_client import AppFlowyError
from audit_log import add_error, finish_audit_log, new_audit_log, write_audit_log
from change_report import new_change_report, set_after, set_before, set_plan, set_summary
from template_render_lib import (
    load_template_payload,
    load_vars_payload,
    render_template_with_vars,
    resolve_template_vars,
    write_render_output,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render AppFlowy template with template variables.")
    parser.add_argument("--template", default=None, help="Template JSON string.")
    parser.add_argument("--template-file", default=None, help="Template JSON file.")
    parser.add_argument("--vars", default=None, help="Template vars JSON string.")
    parser.add_argument("--vars-file", default=None, help="Template vars JSON file.")
    parser.add_argument("--output-file", default=None, help="Write rendered JSON to file.")
    parser.add_argument(
        "--keep-template-vars",
        action="store_true",
        help="Keep template_vars section in rendered output.",
    )
    parser.add_argument(
        "--audit-log-file",
        default=None,
        help="Write execution audit log to file. Default: .tmp/audit_logs/<action>_<time>.json",
    )
    args = parser.parse_args()

    audit = new_audit_log(
        action="render_template",
        target={"type": "template"},
        input_data={
            "template_file": args.template_file,
            "has_template_inline": bool(args.template),
            "vars_file": args.vars_file,
            "has_vars_inline": bool(args.vars),
            "output_file": args.output_file,
            "keep_template_vars": bool(args.keep_template_vars),
        },
    )

    try:
        template = load_template_payload(args.template, args.template_file)
        provided_vars = load_vars_payload(args.vars, args.vars_file)
        resolved_vars, var_meta = resolve_template_vars(template, provided_vars)
        rendered, render_stats = render_template_with_vars(
            template,
            resolved_vars,
            keep_template_vars=bool(args.keep_template_vars),
        )
        output_path = write_render_output(rendered, args.output_file)

        report = new_change_report(
            action="render_template",
            target_type="template",
            target_id=args.template_file or "inline",
            dry_run=True,
            input_data={
                "template_file": args.template_file,
                "vars_file": args.vars_file,
                "keep_template_vars": bool(args.keep_template_vars),
            },
        )
        set_before(report, placeholder_replacements=0)
        set_plan(
            report,
            resolved_var_keys=var_meta.get("resolved_var_keys", []),
            defaulted_vars=var_meta.get("defaulted_vars", []),
        )
        set_after(
            report,
            output_file=output_path,
            placeholder_replacements=render_stats.get("placeholder_replacements", 0),
        )
        set_summary(
            report,
            rendered=True,
            placeholder_replacements=render_stats.get("placeholder_replacements", 0),
            resolved_var_count=len(var_meta.get("resolved_var_keys", [])),
        )

        finish_audit_log(
            audit,
            status="success",
            result={
                "output_file": output_path,
                "placeholder_replacements": render_stats.get("placeholder_replacements", 0),
                "resolved_var_count": len(var_meta.get("resolved_var_keys", [])),
            },
        )
        audit_path = write_audit_log(audit, args.audit_log_file)
        print_json(
            {
                "rendered_template": rendered,
                "resolved_vars": resolved_vars,
                "var_meta": var_meta,
                "render_stats": render_stats,
                "output_file": output_path,
                "audit_log_file": audit_path,
                "change_report": report,
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        add_error(audit, str(exc))
        finish_audit_log(audit, status="failed", result={"error": str(exc)})
        audit_path = write_audit_log(audit, args.audit_log_file)
        raise AppFlowyError(f"render-template failed. audit_log_file={audit_path}. error={exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
