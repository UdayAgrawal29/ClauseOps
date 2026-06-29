import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, request, ApiError } from '../lib/api';
import ContractProgress from '../components/ContractProgress';

// Client-side mirror of the server upload rules (Requirement 3.1-3.3, design §Component 2):
// at most 20MB and must be a PDF. We validate before sending to give immediate feedback.
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_MB = 20;

function formatMb(bytes) {
  return (bytes / (1024 * 1024)).toFixed(1);
}

/**
 * Validate a candidate file client-side. Returns an error string, or null when valid.
 * @param {File|null} file
 */
export function validateContractFile(file) {
  if (!file) return 'Please choose a PDF file to upload.';
  const isPdfType = file.type === 'application/pdf';
  const isPdfExt = /\.pdf$/i.test(file.name);
  if (!isPdfType && !isPdfExt) {
    return 'Only PDF files are accepted. Please choose a .pdf file.';
  }
  if (file.size > MAX_BYTES) {
    return `That file is ${formatMb(file.size)} MB, which exceeds the ${MAX_MB} MB limit.`;
  }
  if (file.size === 0) {
    return 'That file is empty. Please choose a valid PDF.';
  }
  return null;
}

// Map backend rejection statuses to friendly messages (server is the source of truth).
function messageForApiError(err) {
  if (err instanceof ApiError) {
    if (err.status === 413) return `The file is too large. The limit is ${MAX_MB} MB.`;
    if (err.status === 415) return 'The server rejected the file type. Please upload a PDF.';
    if (err.status === 400) return 'The file does not look like a valid PDF. Please try another file.';
    if (err.status === 401) return 'Your session has expired. Please sign in again.';
    return err.message || 'Upload failed. Please try again.';
  }
  return 'Something went wrong while uploading. Please try again.';
}

export default function Upload() {
  const [file, setFile] = useState(null);
  const [clientError, setClientError] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [contract, setContract] = useState(null); // { contract_id, job_id }
  const [progressName, setProgressName] = useState(null); // filename shown in progress view
  const inputRef = useRef(null);
  const queryClient = useQueryClient();

  const uploadMutation = useMutation({
    mutationFn: async (selectedFile) => {
      const form = new FormData();
      form.append('file', selectedFile);
      // Bypass api.post's JSON handling: pass FormData as the raw body so the browser sets
      // the multipart boundary. request() still attaches the Bearer token.
      return request('/contracts', { method: 'POST', body: form });
    },
    onSuccess: (data) => {
      setContract(data);
      setProgressName(file?.name || null);
      // The new contract should appear in the Analysis list and dashboard counts
      // right away rather than after the 30s staleTime.
      queryClient.invalidateQueries({ queryKey: ['contracts'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
    },
  });

  // Bundled sample contracts (served by the deployed app) so a visitor can run the
  // full pipeline without uploading their own file.
  const demosQuery = useQuery({
    queryKey: ['demo-contracts'],
    queryFn: () => api.get('/demo-contracts'),
    staleTime: 5 * 60 * 1000,
  });

  const runDemo = useMutation({
    mutationFn: (slug) => api.post(`/demo-contracts/${slug}`),
    onSuccess: (data, slug) => {
      const d = (Array.isArray(demosQuery.data) ? demosQuery.data : []).find(
        (x) => x.slug === slug,
      );
      setContract(data);
      setProgressName(d ? `${d.title}.pdf` : 'Sample contract.pdf');
      queryClient.invalidateQueries({ queryKey: ['contracts'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
    },
  });

  function selectFile(candidate) {
    const validationError = validateContractFile(candidate);
    if (validationError) {
      setClientError(validationError);
      setFile(null);
      return;
    }
    setClientError(null);
    setFile(candidate);
  }

  function handleInputChange(event) {
    const candidate = event.target.files?.[0] || null;
    selectFile(candidate);
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    const candidate = event.dataTransfer.files?.[0] || null;
    selectFile(candidate);
  }

  function handleSubmit(event) {
    event.preventDefault();
    const validationError = validateContractFile(file);
    if (validationError) {
      setClientError(validationError);
      return;
    }
    uploadMutation.mutate(file);
  }

  function reset() {
    setFile(null);
    setClientError(null);
    setContract(null);
    setProgressName(null);
    uploadMutation.reset();
    runDemo.reset();
    if (inputRef.current) inputRef.current.value = '';
  }

  // After a successful upload, swap the form for the live-progress view.
  if (contract) {
    return (
      <div className="space-y-4">
        <ContractProgress contractId={contract.contract_id} filename={progressName || file?.name} />
        <button
          type="button"
          onClick={reset}
          className="text-sm font-medium text-brand-700 hover:text-brand-800"
        >
          Upload another contract
        </button>
      </div>
    );
  }

  const serverError = uploadMutation.isError ? messageForApiError(uploadMutation.error) : null;
  const errorMessage = clientError || serverError;
  const demos = Array.isArray(demosQuery.data) ? demosQuery.data : [];
  const runningSlug = runDemo.isPending ? runDemo.variables : null;
  const demoError = runDemo.isError ? messageForApiError(runDemo.error) : null;

  return (
    <div className="space-y-5">
      <section className="card p-6">
        <h1 className="text-xl font-semibold text-ink-900">Upload a contract</h1>
        <p className="mt-1 text-sm text-ink-500">
          Upload a contract PDF (up to {MAX_MB} MB). We&apos;ll analyze it and surface your
          deadline-bearing obligations.
        </p>

        <form className="mt-6" onSubmit={handleSubmit}>
          <label
            htmlFor="contract-file"
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center transition-colors ${
              isDragging
                ? 'border-brand-400 bg-brand-50'
                : 'border-ink-300 bg-ink-50 hover:border-ink-400'
            }`}
          >
            <span className="text-sm font-medium text-ink-700">
              {file ? file.name : 'Drag a PDF here, or click to browse'}
            </span>
            <span className="mt-1 text-xs text-ink-400">
              {file ? `${formatMb(file.size)} MB` : `PDF only · max ${MAX_MB} MB`}
            </span>
            <input
              id="contract-file"
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={handleInputChange}
            />
          </label>

          {errorMessage && (
            <p role="alert" className="mt-3 text-sm text-risk-critical">
              {errorMessage}
            </p>
          )}

          <div className="mt-6 flex items-center gap-3">
            <button type="submit" disabled={!file || uploadMutation.isPending} className="btn-primary">
              {uploadMutation.isPending ? 'Uploading…' : 'Upload & analyze'}
            </button>
            {file && !uploadMutation.isPending && (
              <button
                type="button"
                onClick={reset}
                className="text-sm font-medium text-ink-500 hover:text-ink-700"
              >
                Clear
              </button>
            )}
          </div>
        </form>
      </section>

      {/* Sample contracts — bundled with the app so anyone can try the full pipeline. */}
      {demos.length > 0 ? (
        <section className="card p-6">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-ink-900">Or try a sample contract</h2>
              <p className="mt-0.5 text-sm text-ink-500">
                No file handy? Run one of these representative agreements through the full
                analysis.
              </p>
            </div>
          </div>

          {demoError ? (
            <p role="alert" className="mt-3 text-sm text-risk-critical">
              {demoError}
            </p>
          ) : null}

          <ul className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {demos.map((d) => {
              const isRunning = runningSlug === d.slug;
              const disabled = runDemo.isPending;
              return (
                <li key={d.slug}>
                  <button
                    type="button"
                    onClick={() => runDemo.mutate(d.slug)}
                    disabled={disabled}
                    className="group flex h-full w-full flex-col rounded-lg border border-ink-200 bg-white p-4 text-left transition-colors hover:border-ink-300 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="badge badge-neutral">{d.contract_type}</span>
                      <span className="tabular text-2xs text-ink-400">{d.size_kb} KB</span>
                    </div>
                    <p className="mt-2 text-sm font-semibold text-ink-900">{d.title}</p>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-500">
                      {d.description}
                    </p>
                    <span className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-brand-700">
                      {isRunning ? 'Starting…' : 'Run sample'}
                      {!isRunning ? (
                        <span aria-hidden="true" className="transition-transform group-hover:translate-x-0.5">
                          →
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
