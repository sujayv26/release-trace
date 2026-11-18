const labels: Record<string, string> = { block: 'Blocked', fail: 'Failed', pass: 'Passed' };

export function Status({ value }: { value: string | null }) {
  const status = value ?? 'pending';
  const label = labels[status] ?? status;
  return <span className="status" data-status={status}>{label}</span>;
}

const utcTime = new Intl.DateTimeFormat('en-GB', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
});

export function RunTime({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{utcTime.format(new Date(value))} UTC</time> : <span>Unavailable</span>;
}
