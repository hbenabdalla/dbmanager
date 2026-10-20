import { Link } from "react-router-dom";

/** Where guarded administration lives: the server page's Administration tab. */
export function adminTabPath(serverId: string, action?: string): string {
  return `/servers/${serverId}?tab=administration${action ? `&action=${encodeURIComponent(action)}` : ""}`;
}

/** Accurate pointer to the guarded administration actions (preview → review → confirm → audited). */
export function AdministrationHint({ serverId, what, action }: { serverId: string; what: string; action?: string }) {
  return (
    <span className="text-muted">
      {what} are made from the server&apos;s{" "}
      <Link className="font-medium text-accent hover:underline" to={adminTabPath(serverId, action)}>Administration tab</Link>
      : DBManager generates the statements for review, executes them with the administration credential after confirmation, and audits them.
      This needs the server registered with administration enabled and the matching permission.
    </span>
  );
}
