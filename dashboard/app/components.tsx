const labels: Record<string, string> = {
  block: 'Blocked', fail: 'Failed', pass: 'Passed', promoted: 'Promoted', aborted: 'Aborted',
};

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
import type { Variant } from '../lib/releases';


const evidenceReasons: Record<string, string> = {
  build_mismatch: 'The endpoint reported a different build.',
  citation_unverified: 'The citation could not be verified against the case policies.',
  endpoint_error: 'The endpoint request failed.',
  endpoint_timeout: 'The endpoint timed out.',
  invalid_response: 'The endpoint returned an invalid response.',
  response_too_large: 'The endpoint response exceeded the size limit.',
  unsupported_encoding: 'The endpoint returned an unsupported encoding.',
};

export function Evidence({ label, variant }: { label: string; variant: Variant }) {
  return (
    <div className="evidence">
      <h3>{label} <Status value={variant.outcome} /></h3>
      {variant.evidence ? <><code>{variant.evidence.id}</code><blockquote>{variant.evidence.text}</blockquote></> : <p>{evidenceReasons[variant.reason ?? ''] ?? 'No policy evidence recorded.'}</p>}
    </div>
  );
}
