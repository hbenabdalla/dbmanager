import { PostgreSQL, sql, MSSQL, MySQL, PLSQL, StandardSQL } from "@codemirror/lang-sql";
import CodeMirror, { keymap, type ReactCodeMirrorProps } from "@uiw/react-codemirror";
import { useMemo } from "react";
import { useTheme } from "@/lib/theme";

const DIALECTS = { postgresql: PostgreSQL, mysql: MySQL, mariadb: MySQL, mssql: MSSQL, oracle: PLSQL } as const;

export function SqlEditor({ value, onChange, engine = "postgresql", onRun, height = "220px", readOnly }: {
  value: string; onChange?: (v: string) => void; engine?: string; onRun?: () => void; height?: string; readOnly?: boolean;
}) {
  const { theme } = useTheme();
  const extensions = useMemo(() => {
    const dialect = DIALECTS[engine as keyof typeof DIALECTS] ?? StandardSQL;
    const ext: ReactCodeMirrorProps["extensions"] = [sql({ dialect, upperCaseKeywords: true })];
    if (onRun) ext.push(keymap.of([{ key: "Mod-Enter", run: () => { onRun(); return true; } }]));
    return ext;
  }, [engine, onRun]);
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <CodeMirror value={value} onChange={onChange} extensions={extensions} height={height} theme={theme} readOnly={readOnly}
        basicSetup={{ lineNumbers: true, foldGutter: false, highlightActiveLine: true, autocompletion: true }} aria-label="SQL editor" />
    </div>
  );
}
