/* Compatibility for the copied UI. All personal data stays in the Python API. */
window.openPythonPlaylist = async function (id) {
    const host = document.getElementById('playlistsContent');
    if (!host) return;
    host.replaceChildren();
    const status = document.createElement('p');
    status.textContent = '재생목록을 불러오는 중입니다.';
    host.append(status);
    try {
        const response = await fetch('/api/playlists/' + encodeURIComponent(id) + '/videos');
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.message || '불러오지 못했습니다.');
        host.replaceChildren();
        const back = document.createElement('button');
        back.textContent = '재생목록 목록';
        back.onclick = () => document.getElementById('spaPlaylistsBtn')?.click();
        host.append(back);
        const title = document.createElement('h3');
        title.textContent = data.title;
        host.append(title);
        const grid = document.createElement('div');
        grid.className = 'bookmark-grid';
        for (const video of data.videos) {
            const link = document.createElement('a');
            link.className = 'bookmark-card';
            link.href = '/watch?v=' + encodeURIComponent(video.videoId);
            const image = document.createElement('img');
            image.className = 'bookmark-thumb'; image.src = video.thumbnail; image.alt = ''; image.loading = 'lazy';
            const label = document.createElement('h3'); label.className = 'bookmark-title'; label.textContent = video.title;
            link.append(image, label); grid.append(link);
        }
        host.append(grid);
        if (!data.videos.length) {
            const empty = document.createElement('p'); empty.textContent = '저장된 영상이 없습니다. 영상의 저장 버튼에서 추가해 주세요.'; host.append(empty);
        }
    } catch (error) { status.textContent = error.message; host.replaceChildren(status); }
};

// The API always returns a partially masked name; clicking only removes blur.
const hiddenNames = new WeakSet();
function blurYouTubeNames() {
    const youtubeTab = document.getElementById('tabYoutubeComments');
    if (!youtubeTab || !youtubeTab.classList.contains('active')) return;
    document.querySelectorAll('.comment-author, .comment-nickname, .comment-user-name').forEach(node => {
        if (hiddenNames.has(node)) return;
        hiddenNames.add(node);
        node.style.filter = 'blur(5px)'; node.style.cursor = 'pointer'; node.tabIndex = 0;
        node.title = '눌러서 일부 가려진 이름 보기';
        const reveal = () => { node.style.filter = ''; };
        node.addEventListener('click', reveal);
        node.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); reveal(); } });
    });
}
const commentArea = document.getElementById('commentContainer') || document.getElementById('commentsList') || document.getElementById('commentList');
if (commentArea) new MutationObserver(blurYouTubeNames).observe(commentArea, {childList:true,subtree:true});
