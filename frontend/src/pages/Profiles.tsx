import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Drawer, Menu, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { useEffect, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCircleExclamation, faEllipsis, faPlus, faSliders, faStar, faTrashCan, faUserGroup, faUserPlus,
} from '@fortawesome/free-solid-svg-icons';
import {
  getProfiles, createProfile, updateProfile, deleteProfile, bulkAssignProfile, getAlbums, getArtists,
} from '../api';
import type { Profile } from '../types';
import { errorMessage } from '../notify';
import { plural } from '../format';
import {
  EmptyState, IconButton, ListSkeleton, PageHeader, Panel, SaveBar, Tag, useConfirm,
} from '../components/ui';
import { ProfileEditor } from './manage/ProfileEditor';
import {
  DEFAULT_FORM, formFromProfile, sameForm, SELECTION_MODE_SHORT, type ProfileForm,
} from './manage/profileForm';
import c from './manage/Profiles.module.css';

type Selection = number | 'new' | null;

// Query keys start with 'albums'/'artists' so bulk assignment and library
// refreshes (which invalidate those families) update the counts too.
const albumCountKey = (id: number | 'all') => ['albums', 'profile-count', id] as const;
const artistCountKey = (id: number | 'all') => ['artists', 'profile-count', id] as const;

/** A default profile literally named "Default" needs no "Default" tag next to it. */
const namedDefault = (name: string) => name.trim().toLowerCase() === 'default';

function summary(p: Profile): string[] {
  const parts: string[] = [];
  if (p.download_covers) parts.push((p.cover_format || 'jpg').toUpperCase());
  else parts.push('No covers');
  if (!p.download_lyrics) parts.push('No lyrics');
  else {
    parts.push(SELECTION_MODE_SHORT[p.lyrics_selection_mode] ?? 'Best score');
    // The old table's "Synced lyrics: Preferred" column (redundant with the Prefer synced mode).
    if (p.prefer_synced_lyrics && p.lyrics_selection_mode !== 'prefer_synced') parts.push('Synced preferred');
  }
  if (p.auto_translate && p.auto_translate !== 'off') parts.push(`Translate to ${(p.translate_target_lang || 'en').toUpperCase()}`);
  return parts;
}

export default function ProfilesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const narrow = useMediaQuery('(max-width: 1023px)');
  const phone = useMediaQuery('(max-width: 479px)');
  const { data: profiles = [], isLoading, isError, error, refetch } = useQuery({ queryKey: ['profiles'], queryFn: getProfiles });

  const [selection, setSelection] = useState<Selection>(null);
  const [form, setForm] = useState<ProfileForm>(DEFAULT_FORM);
  const [baseline, setBaseline] = useState<ProfileForm>(DEFAULT_FORM);
  const [nameError, setNameError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const current = typeof selection === 'number' ? profiles.find(p => p.id === selection) ?? null : null;
  const dirty = selection === 'new' || !sameForm(form, baseline);

  const albumCounts = useQueries({
    queries: profiles.map(p => ({
      queryKey: albumCountKey(p.id),
      queryFn: () => getAlbums({ profileId: p.id, pageSize: 1 }).then(r => r.total),
      staleTime: 60_000,
    })),
  });

  const load = (p: Profile) => {
    const f = formFromProfile(p);
    setSelection(p.id);
    setForm(f);
    setBaseline(f);
    setNameError(null);
  };

  // Pick the default profile once the list arrives; follow deletions.
  useEffect(() => {
    if (!profiles.length) return;
    if (selection === null || (typeof selection === 'number' && !profiles.some(p => p.id === selection))) {
      load(profiles.find(p => p.is_default) ?? profiles[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles, selection]);

  const confirmDiscard = async () => !dirty || confirm({
    title: 'Discard unsaved changes?',
    message: selection === 'new'
      ? 'The new profile hasn’t been created yet. Discard it?'
      : <>Your changes to <strong>{current?.name ?? 'this profile'}</strong> haven’t been saved.</>,
    confirmLabel: 'Discard changes',
    tone: 'danger',
  });

  const select = async (p: Profile) => {
    if (selection === p.id) { if (narrow) setDrawerOpen(true); return; }
    if (!(await confirmDiscard())) return;
    load(p);
    if (narrow) setDrawerOpen(true);
  };

  const openCreate = async () => {
    if (!(await confirmDiscard())) return;
    setSelection('new');
    setForm(DEFAULT_FORM);
    setBaseline(DEFAULT_FORM);
    setNameError(null);
    if (narrow) setDrawerOpen(true);
  };

  const discard = () => {
    if (selection === 'new' || !current) {
      const fallback = profiles.find(p => p.is_default) ?? profiles[0];
      if (fallback) load(fallback); else { setForm(DEFAULT_FORM); setBaseline(DEFAULT_FORM); setSelection(null); }
      if (narrow) setDrawerOpen(false);
    } else {
      setForm(baseline);
      setNameError(null);
    }
  };

  const closeDrawer = async () => {
    if (!(await confirmDiscard())) return;
    if (dirty) discard();
    setDrawerOpen(false);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        cover_providers: JSON.stringify(form.cover_providers),
        lyrics_providers: JSON.stringify(form.lyrics_providers),
      };
      if (typeof selection === 'number') return updateProfile(selection, payload);
      return createProfile(payload);
    },
    onSuccess: (saved: Profile | undefined) => {
      notifications.show({ title: 'Saved', message: 'Profile saved', color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      setBaseline(form);
      if (saved && typeof saved.id === 'number') {
        // Put a created profile in the list right away, so the selection
        // doesn't fall back to the default before the refetch lands.
        queryClient.setQueryData<Profile[]>(['profiles'], old =>
          old && !old.some(p => p.id === saved.id) ? [...old, saved] : old);
        setSelection(saved.id);
      }
    },
    onError: (err: unknown) => {
      notifications.show({ title: 'Error', message: errorMessage(err, 'Failed'), color: 'danger' });
    },
  });

  const save = () => {
    if (!form.name.trim()) {
      setNameError('Profile name is required');
      return;
    }
    setNameError(null);
    saveMutation.mutate();
  };

  const deleteMutation = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => {
      notifications.show({ title: 'Deleted', message: 'Profile deleted', color: 'success' });
      setSelection(null);
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['artists'] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
      if (narrow) setDrawerOpen(false);
    },
    onError: (err: unknown) => {
      notifications.show({ title: 'Error', message: errorMessage(err, 'Failed'), color: 'danger' });
    },
  });

  const setDefaultMutation = useMutation({
    mutationFn: (id: number) => updateProfile(id, { is_default: true }),
    onSuccess: () => {
      notifications.show({ title: 'Done', message: 'Default profile updated', color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
    onError: (err: unknown) => {
      notifications.show({ title: 'Error', message: errorMessage(err, 'Failed to set default profile'), color: 'danger' });
    },
  });

  const bulkAssignMutation = useMutation({
    mutationFn: (data: { profileId: number; mode: 'all' | 'unassigned' }) => bulkAssignProfile(data),
    onSuccess: (data: any) => {
      notifications.show({ title: 'Done', message: data.message, color: 'success' });
      queryClient.invalidateQueries({ queryKey: ['artists'] });
      queryClient.invalidateQueries({ queryKey: ['albums'] });
    },
    onError: (err: unknown) => {
      notifications.show({ title: 'Error', message: errorMessage(err, 'Failed'), color: 'danger' });
    },
  });

  /** Counts for the assign confirmation; null when they can't be fetched. */
  const assignCounts = async (mode: 'all' | 'unassigned'): Promise<{ artists: number; albums: number } | null> => {
    try {
      const fetchTotal = (key: readonly unknown[], fn: () => Promise<number>) =>
        queryClient.fetchQuery({ queryKey: key, queryFn: fn, staleTime: 30_000 });
      const [artists, albums] = await Promise.all([
        fetchTotal(artistCountKey('all'), () => getArtists({ pageSize: 1 }).then(r => r.total)),
        fetchTotal(albumCountKey('all'), () => getAlbums({ pageSize: 1 }).then(r => r.total)),
      ]);
      if (mode === 'all') return { artists, albums };
      const [artistsAssigned, albumsAssigned] = await Promise.all([
        Promise.all(profiles.map(p => fetchTotal(artistCountKey(p.id), () => getArtists({ profileId: p.id, pageSize: 1 }).then(r => r.total)))),
        Promise.all(profiles.map(p => fetchTotal(albumCountKey(p.id), () => getAlbums({ profileId: p.id, pageSize: 1 }).then(r => r.total)))),
      ]);
      const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
      return { artists: Math.max(0, artists - sum(artistsAssigned)), albums: Math.max(0, albums - sum(albumsAssigned)) };
    } catch {
      return null;
    }
  };

  const assign = async (p: Profile, mode: 'all' | 'unassigned') => {
    const counts = await assignCounts(mode);
    const what = counts ? `${plural(counts.artists, 'artist')} and ${plural(counts.albums, 'album')}` : null;
    const message = mode === 'all' ? (
      <>
        Assign <strong>{p.name}</strong> to {what ? <>all <strong>{what}</strong></> : 'every artist and album'}?
        {' '}This replaces the profile they use now.
      </>
    ) : counts && counts.artists === 0 && counts.albums === 0 ? (
      <>Every artist and album already has a profile, so nothing will change.</>
    ) : (
      <>
        Assign <strong>{p.name}</strong> to {what ? <>the <strong>{what}</strong></> : 'every artist and album'} without
        a profile? Ones that already have a profile keep it.
      </>
    );
    const ok = await confirm({
      title: mode === 'all' ? 'Assign to all artists and albums?' : 'Assign to unassigned only?',
      message,
      confirmLabel: 'Assign profile',
    });
    if (ok) bulkAssignMutation.mutate({ profileId: p.id, mode });
  };

  const remove = async (p: Profile) => {
    const ok = await confirm({
      title: 'Delete profile?',
      message: <>Delete <strong>{p.name}</strong>? Artists and albums that use it are left without a profile.</>,
      confirmLabel: 'Delete profile',
      tone: 'danger',
    });
    if (ok) deleteMutation.mutate(p.id);
  };

  // Follows the name field as you type, like the list item does.
  const editorTitle = form.name.trim() || (selection === 'new' ? 'New profile' : (current?.name || 'Profile'));
  const editorActions = current ? (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <IconButton icon={faEllipsis} label={`More actions for ${current.name}`} />
      </Menu.Target>
      <Menu.Dropdown>
        {!current.is_default && (
          <Menu.Item leftSection={<FontAwesomeIcon icon={faStar} />} onClick={() => setDefaultMutation.mutate(current.id)}>
            Set as default
          </Menu.Item>
        )}
        <Menu.Label>Assign</Menu.Label>
        <Menu.Item leftSection={<FontAwesomeIcon icon={faUserGroup} />} onClick={() => assign(current, 'all')}>
          Assign to all artists/albums
        </Menu.Item>
        <Menu.Item leftSection={<FontAwesomeIcon icon={faUserPlus} />} onClick={() => assign(current, 'unassigned')}>
          Assign to unassigned only
        </Menu.Item>
        {!current.is_default && (
          <>
            <Menu.Divider />
            <Menu.Item color="danger" leftSection={<FontAwesomeIcon icon={faTrashCan} />} onClick={() => remove(current)}>
              Delete
            </Menu.Item>
          </>
        )}
      </Menu.Dropdown>
    </Menu>
  ) : undefined;

  const editor = (
    <>
      <ProfileEditor
        form={form}
        onChange={f => { setForm(f); if (nameError && f.name.trim()) setNameError(null); }}
        nameError={nameError}
        idPrefix={narrow ? 'profile-drawer' : 'profile'}
      />
      <SaveBar
        dirty={dirty}
        saving={saveMutation.isPending}
        onSave={save}
        onDiscard={discard}
        message={phone ? 'Unsaved' : selection === 'new' ? 'New profile, not created yet' : 'Unsaved changes'}
        saveLabel={selection === 'new' ? (phone ? 'Create' : 'Create profile') : (phone ? 'Save' : 'Save changes')}
      />
    </>
  );

  const editorTitleNode = (
    <span className={c.editorTitle}>
      <span className={c.editorName}>{editorTitle}</span>
      {current?.is_default && !namedDefault(editorTitle) && <Tag>Default</Tag>}
      {selection === 'new' && <Tag>Draft</Tag>}
    </span>
  );

  let content;
  if (isLoading) {
    content = <Panel padding="none"><ListSkeleton rows={4} avatar={false} /></Panel>;
  } else if (isError) {
    content = (
      <Panel>
        <EmptyState
          size="sm"
          tone="danger"
          icon={faCircleExclamation}
          title="Couldn't load profiles"
          message={errorMessage(error)}
          action={<Button variant="default" onClick={() => refetch()}>Retry</Button>}
        />
      </Panel>
    );
  } else {
    const list = (
      <Panel title="Profiles" padding="none" className={c.listPanel}>
        {profiles.length === 0 && selection !== 'new' ? (
          <EmptyState size="sm" icon={faSliders} title="No profiles yet" message="Create one to control what gets downloaded." />
        ) : (
          <ul className={c.list} aria-label="Profiles">
            {selection === 'new' && (
              <li>
                <UnstyledButton className={c.item} data-active aria-current="true" onClick={() => narrow && setDrawerOpen(true)}>
                  <span className={c.itemHead}>
                    <span className={c.itemName}>{form.name.trim() || 'New profile'}</span>
                    <Tag>Draft</Tag>
                  </span>
                  <span className={c.itemMeta}>Not created yet</span>
                </UnstyledButton>
              </li>
            )}
            {profiles.map((p, i) => {
              const count = albumCounts[i]?.data;
              const active = selection === p.id;
              return (
                <li key={p.id}>
                  <UnstyledButton
                    className={c.item}
                    data-active={active || undefined}
                    aria-current={active ? 'true' : undefined}
                    onClick={() => select(p)}
                  >
                    <span className={c.itemHead}>
                      <span className={c.itemName}>{active ? (form.name.trim() || p.name) : p.name}</span>
                      {p.is_default && !namedDefault(active ? (form.name.trim() || p.name) : p.name) && <Tag>Default</Tag>}
                      {active && dirty && (
                        <span className={c.itemDirty} title="Unsaved changes">
                          <span className="visually-hidden">Unsaved changes</span>
                        </span>
                      )}
                    </span>
                    <span className={c.itemMeta}>
                      {count === undefined ? 'Counting albums…' : `Applies to ${plural(count, 'album')}`}
                    </span>
                    <span className={c.itemTags}>
                      {summary(p).map(s => <span key={s} className={c.itemTag}>{s}</span>)}
                    </span>
                  </UnstyledButton>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    );

    content = narrow ? (
      <>
        {list}
        {/* Compound form so the ⋯ menu sits in the header next to the close button. */}
        <Drawer.Root opened={drawerOpen} onClose={closeDrawer} position="right" size="100%">
          <Drawer.Overlay blur={2} />
          <Drawer.Content>
            <Drawer.Header>
              <Drawer.Title className={c.drawerTitle}>{editorTitleNode}</Drawer.Title>
              <span className={c.drawerActions}>
                {editorActions}
                <Drawer.CloseButton aria-label="Close editor" />
              </span>
            </Drawer.Header>
            <Drawer.Body className={c.drawerBody}>{editor}</Drawer.Body>
          </Drawer.Content>
        </Drawer.Root>
      </>
    ) : (
      <div className={c.layout}>
        {list}
        <Panel title={editorTitleNode} actions={editorActions} className={c.editorPanel} aria-label="Profile editor">
          {selection === null ? (
            <EmptyState size="sm" icon={faSliders} title="Select a profile" message="Pick a profile on the left to edit it." />
          ) : editor}
        </Panel>
      </div>
    );
  }

  return (
    <div className="ly-page">
      <PageHeader
        title="Profiles"
        count={isLoading || isError ? undefined : profiles.length}
        subtitle="Control what metadata gets downloaded per artist or album"
        actions={(
          <Button variant="filled" leftSection={<FontAwesomeIcon icon={faPlus} />} onClick={openCreate}>
            New profile
          </Button>
        )}
      />
      {content}
    </div>
  );
}
