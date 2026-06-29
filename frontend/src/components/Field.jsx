// Accessible labeled text input for the auth forms. Associates the label with the input,
// wires aria-invalid / aria-describedby for screen readers, and renders inline field errors.
export default function Field({ id, label, type = 'text', registration, error, ...rest }) {
  const errorId = `${id}-error`;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-ink-700">
        {label}
      </label>
      <input
        id={id}
        type={type}
        className="field aria-[invalid=true]:border-red-500 aria-[invalid=true]:focus:ring-red-500/25"
        aria-invalid={error ? 'true' : 'false'}
        aria-describedby={error ? errorId : undefined}
        {...registration}
        {...rest}
      />
      {error ? (
        <p id={errorId} className="text-xs text-risk-critical">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}
