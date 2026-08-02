type Props = {
  url?: string | null;
  label?: string;
};

/** External deep-link to a Prefect flow run (opens in new tab). */
export default function PrefectLink({ url, label = "Prefect" }: Props) {
  if (!url) return null;
  return (
    <a className="prefect-link" href={url} target="_blank" rel="noreferrer">
      {label}
    </a>
  );
}
