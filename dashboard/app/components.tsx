const labels: Record<string, string> = { block: 'Blocked', fail: 'Failed', pass: 'Passed' };

export function Status({ value }: { value: string | null }) {
  const status = value ?? 'pending';
  const label = labels[status] ?? status;
  return <span className="status" data-status={status}>{label}</span>;
}
