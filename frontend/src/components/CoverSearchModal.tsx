import { useState, type MouseEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Loader, Modal } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faCircleExclamation, faExpand, faImage, faRotateRight } from '@fortawesome/free-solid-svg-icons';
import { notifyError } from '../notify';
import { searchCovers, downloadCover } from '../api';
import { CoverArt, EmptyState, GridSkeleton, IconButton, Tag } from './ui';
import { plural } from '../format';
import ImageLightbox from './ImageLightbox';
import c from './CoverSearchModal.module.css';

/** One provider result from GET /metadata/covers/search. */
interface CoverResult {
  url: string;
  url_small?: string;
  url_large?: string;
  provider: string;
  type?: string;
  /** "1200x1200" when the provider reports it. */
  size?: string;
}

interface Props {
  albumId: number;
  opened: boolean;
  onClose: () => void;
  /** For the title and the pinned "Current" cover. */
  album?: { title?: string; cover?: string | null; cover_status?: string | null };
}

function thumbOf(r: CoverResult): string {
  return r.url_large || r.url_small || r.url;
}

function sizeLabel(size?: string): string | null {
  if (!size) return null;
  return size.replace(/\s*[xX]\s*/, '×');
}

/**
 * Cover art picker (DESIGN.md 8.16): searches providers on open; one click (or
 * Enter) on a tile saves that cover. A corner button previews the large image
 * without saving. The album's current cover is pinned first.
 */
export default function CoverSearchModal({ albumId, opened, onClose, album }: Props) {
  const queryClient = useQueryClient();
  const fullScreen = useMediaQuery('(max-width: 767px)') ?? false;
  const [saving, setSaving] = useState<number | null>(null);
  const [preview, setPreview] = useState<{ src: string; alt: string } | null>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['cover-search', albumId],
    queryFn: () => searchCovers(albumId),
    enabled: opened,
  });

  const downloadMutation = useMutation({
    mutationFn: (result: CoverResult) => downloadCover(albumId, { url: result.url, provider: result.provider }),
    onSuccess: () => {
      notifications.show({ title: 'Cover saved', message: album?.title ? `New cover art saved for ${album.title}.` : 'Cover art saved.', color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['album', String(albumId)] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
      queryClient.invalidateQueries({ queryKey: ['artist-albums'] });
      onClose();
    },
    // 409 reasons verbatim: folder not reachable, no album folder of its own, …
    onError: (err) => notifyError('Cover not saved', err, 'The cover could not be downloaded.'),
    onSettled: () => setSaving(null),
  });

  const results: CoverResult[] = data?.results || [];
  const busy = downloadMutation.isPending;
  const current = album?.cover && album.cover_status && album.cover_status !== 'missing' ? album.cover : null;
  const title = album?.title ? `Search cover art — ${album.title}` : 'Search cover art';

  const save = (r: CoverResult, i: number) => {
    if (busy) return;
    setSaving(i);
    downloadMutation.mutate(r);
  };

  // Keyboard focus on one tile + the mouse over another would show two "Use this
  // cover" overlays. The CSS hides the hover one while a tile has keyboard focus;
  // moving the mouse over another tile moves focus there, so the single
  // highlighted tile is always the one Enter saves.
  const followPointer = (e: MouseEvent<HTMLButtonElement>) => {
    const tile = e.currentTarget;
    const active = document.activeElement;
    if (active === tile || !(active instanceof HTMLButtonElement) || !active.classList.contains(c.face)) return;
    if (tile.closest('ul')?.contains(active)) tile.focus({ preventScroll: true });
  };

  let body;
  if (isLoading) {
    body = (
      <div className={c.loading} aria-live="polite">
        <p className={c.lead}><Loader size="xs" /> Searching providers…</p>
        <GridSkeleton count={8} min={180} delay={0} />
      </div>
    );
  } else if (isError) {
    body = (
      <EmptyState
        size="sm"
        tone="danger"
        icon={faCircleExclamation}
        title="Couldn't search cover providers"
        message={(error as Error)?.message}
        action={<Button variant="default" leftSection={<FontAwesomeIcon icon={faRotateRight} />} loading={isFetching} onClick={() => refetch()}>Retry</Button>}
      />
    );
  } else if (results.length === 0 && !current) {
    body = <EmptyState size="sm" icon={faImage} title="No cover art found from any provider." />;
  } else {
    body = (
      <>
        <p className={c.lead}>
          {results.length === 0 ? 'No new results' : `${plural(results.length, 'result')} · click a cover to save it`}
        </p>
        <ul className={c.grid} aria-label="Cover art results" data-busy={busy || undefined}>
          {current && (
            <li className={c.tile} data-current="">
              <div className={c.face}>
                <CoverArt src={current} alt="" kind="album" name={album?.title} size="fill" radius={12} />
              </div>
              <div className={c.footer}>
                <Tag>Current</Tag>
                <span className={c.meta}>In use</span>
              </div>
              <IconButton
                className={c.previewBtn}
                icon={faExpand}
                label="Preview current cover"
                variant="default"
                size="sm"
                onClick={() => setPreview({ src: current, alt: album?.title ? `Current cover of ${album.title}` : 'Current cover' })}
              />
            </li>
          )}
          {results.map((r, i) => {
            const size = sizeLabel(r.size);
            const isSaving = saving === i;
            return (
              <li key={`${r.provider}-${i}`} className={c.tile} data-saving={isSaving || undefined}>
                <button
                  type="button"
                  className={c.face}
                  disabled={busy}
                  aria-label={`Use this cover from ${r.provider}${size ? `, ${size}` : ''}`}
                  onClick={() => save(r, i)}
                  onMouseMove={followPointer}
                >
                  <CoverArt src={thumbOf(r)} alt="" kind="album" size="fill" radius={12} />
                  {/* Until the image arrives (some providers redirect several times) */}
                  <span className={c.pending} aria-hidden="true"><Loader size="sm" className={c.pendingLoader} /></span>
                  <span className={c.overlay} aria-hidden="true">
                    {isSaving
                      ? <Loader size="sm" />
                      : <span className={c.use}><FontAwesomeIcon icon={faCheck} />Use this cover</span>}
                  </span>
                </button>
                <div className={c.footer}>
                  <Tag>{r.provider}</Tag>
                  {/* Providers often omit the size; the provider type ("front") is jargon. */}
                  {size && <span className={c.meta}>{size}</span>}
                </div>
                <IconButton
                  className={c.previewBtn}
                  icon={faExpand}
                  label={`Preview cover from ${r.provider} (${i + 1} of ${results.length})`}
                  variant="default"
                  size="sm"
                  disabled={busy}
                  onClick={() => setPreview({ src: r.url_large || r.url, alt: `Cover from ${r.provider}` })}
                />
              </li>
            );
          })}
        </ul>
      </>
    );
  }

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={title}
        size={960}
        fullScreen={fullScreen}
        // The preview is its own dialog on top and swallows Esc while open. Don't
        // toggle trapFocus here: Mantine's focus-return re-records the active
        // element when it changes, which strands focus after the preview closes.
        // The modal's Tab trap is a no-op while focus sits in the lightbox.
        closeOnEscape={!preview}
        closeButtonProps={{ 'aria-label': 'Close' }}
        classNames={{ title: c.title }}
      >
        {body}
      </Modal>
      {preview && <ImageLightbox src={preview.src} alt={preview.alt} onClose={() => setPreview(null)} />}
    </>
  );
}
