import { useEffect, useState } from "react";
import { ActivityEvent, api } from "../api";

export default function ActivityTab({ projectId, version }: { projectId: string; version: number }) {
  const [events, setEvents] = useState<ActivityEvent[]>([]);

  useEffect(() => {
    api.events(projectId).then((e) => setEvents(e.slice(-200).reverse()));
  }, [projectId, version]);

  return (
    <div className="space-y-1">
      {events.map((e) => (
        <div key={e.id} className="flex items-baseline gap-3 text-sm border-b border-slate-900 py-1.5">
          <span className="font-mono text-emerald-400">{e.type}</span>
          <span className="text-slate-400 truncate">{e.payload === "{}" ? "" : e.payload}</span>
          <span className="ml-auto text-xs text-slate-500 whitespace-nowrap">
            {new Date(e.created_at).toLocaleTimeString()}
          </span>
        </div>
      ))}
      {events.length === 0 && <p className="text-sm text-slate-500">No activity yet.</p>}
    </div>
  );
}
