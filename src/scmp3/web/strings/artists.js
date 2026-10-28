// Text for the artist editor: the main and featured rows, each name's menu, and what saves.
export default {
  rows: { main: "Main artist", featured: "Featured" },
  add: "+ Add",
  addLabel: { main: "Add a main artist", featured: "Add a featured artist" },
  addPlaceholder: "Name, then Enter",
  remove: (name) => `Remove ${name}`,
  // Read by screen readers on each name, since the keys aren't shown.
  tagHint: "Arrow keys move between names. Enter opens the menu, and Delete removes the name.",
  emptyRow: { main: "No main artist", featured: "No featured artists" },
  reset: "Reset",
  resetLabel: "Reset the artists to the suggestion",
  menu: {
    toFeatured: "Make featured",
    toMain: "Make main",
    left: "Move left",
    right: "Move right",
    remove: "Remove",
  },
  // What the tags and the folder will be, as Python renders them.
  savesAs: "Saves as",
  savedTitle: (title) => `“${title}”`,
  savedArtist: (artist) => `by “${artist}”`,
  noArtist: "with no artist",
  needsMainArtist: "Move a name to Main artist before saving.",
  savedFolder: (folder) => `in the folder “${folder}”`,
  typeCredit: "Type the credit yourself",
  useList: "Use the artist list",
  typedLabel: "Artist",
  // The set's track table: the button that opens a track's artists, and the popup it opens.
  artistsOf: (title) => `Artists of ${title}`,
  noArtistCell: "No artist",
};
