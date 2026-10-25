package cz.cztvhub.core.archive

/** One of the 14 categories of the Archiv menu (`GET /dvb/archive/categories`), in the gateway's order. */
data class ArchiveCategory(val id: String, val title: String)

/** An item of the Archiv menu (spec/apps.md "Archiv", T23 layout); [key] is the golden id (`archive_cases.json` "menu"). */
sealed interface MenuItem {
    val key: String

    data object Search : MenuItem { override val key = "search" }
    data object Continue : MenuItem { override val key = "continue" }
    data object Recordings : MenuItem { override val key = "recordings" }
    data class Category(val category: ArchiveCategory) : MenuItem { override val key get() = category.id }

    /** "‹ Filmy": the first item of a sub-menu, back to the main menu. */
    data object Back : MenuItem { override val key = "back" }

    /** "Všechny": the whole category. */
    data object All : MenuItem { override val key = "all" }
    data class Genre(val genre: ArchiveGenre) : MenuItem { override val key get() = genre.id }
}

/** What the grid shows; [Category.genre] null = the whole category. */
sealed interface GridContent {
    data object Continue : GridContent
    data object Recordings : GridContent
    data object Search : GridContent
    data class Category(val slug: String, val genre: String? = null) : GridContent
}

/**
 * The Archiv menu: [category] is the open sub-menu (null = main menu), [selected] the item the grid follows,
 * [grid] what it shows, [sort] the sort chips' choice for it (kept while one category is shown, reset for another).
 */
data class MenuState(
    val categories: List<ArchiveCategory>,
    val category: ArchiveCategory?,
    val genres: List<ArchiveGenre>,
    val selected: MenuItem,
    val grid: GridContent,
    val sort: ArchiveSort,
) {
    val isSubMenu: Boolean get() = category != null

    /** Main menu: Hledat, Pokračovat ve sledování, Naše nahrávky, the categories; sub-menu: ‹ title, Všechny, sub-genres. */
    val items: List<MenuItem>
        get() = if (category == null) {
            listOf(MenuItem.Search, MenuItem.Continue, MenuItem.Recordings) + categories.map(MenuItem::Category)
        } else {
            listOf(MenuItem.Back, MenuItem.All) + genres.map(MenuItem::Genre)
        }

    /** The sub-menu's first item, "‹ Filmy"; null in the main menu. */
    val title: String? get() = category?.let { "‹ ${it.title}" }

    /** The sort chips above the grid, the first one the default; none for "Pokračovat" and the search. */
    val sorts: List<ArchiveSort> get() = ArchiveMenu.sorts(grid)
}

/** What OK or Back on the menu does. */
sealed interface MenuStep {
    /** The menu now; [focusSearchField] = the focus goes into the search field (OK on "Hledat"). */
    data class Show(val state: MenuState, val focusSearchField: Boolean = false) : MenuStep

    /** Back in the main menu: the tab is left as elsewhere. */
    data object Leave : MenuStep
}

/** Back in the search field or its results: clear the search first, then back to the menu. */
enum class SearchBack { CLEAR, MENU }

/**
 * The Archiv menu as a state machine (spec/apps.md "Archiv", T23; golden `archive_cases.json` "menu"). Pure; the
 * screen calls [settle] when the focus rests on an item for [SETTLE_MS], [ok] on OK, [back] on Back/Menu/Return.
 */
object ArchiveMenu {
    /** Moving the focus in the menu changes the grid after this long without moving. */
    const val SETTLE_MS = 400L

    /** The tab opens on "Pokračovat ve sledování" when this TV has an entry for it, else on the first category. */
    fun open(categories: List<ArchiveCategory>, continueEntries: Int): MenuState {
        val first = categories.firstOrNull()
        val selected: MenuItem = when {
            continueEntries > 0 -> MenuItem.Continue
            first != null -> MenuItem.Category(first)
            // No category list (the gateway did not answer): our recordings, which says why when it fails too.
            else -> MenuItem.Recordings
        }
        return MenuState(categories, null, emptyList(), selected, gridOf(selected, null), ArchiveSort.POPULAR)
            .let { it.copy(sort = defaultSort(it.grid)) }
    }

    /** The focus rested on [item]: the grid shows what it filters. A category does not open its sub-menu here. */
    fun settle(state: MenuState, item: MenuItem): MenuState {
        if (item !in state.items || item == MenuItem.Back) return state
        return select(state, item)
    }

    /** OK on [item]: as [settle] at once; a category opens its sub-menu, "‹" is Back, "Hledat" focuses the field. */
    fun ok(state: MenuState, item: MenuItem): MenuStep {
        if (item !in state.items) return MenuStep.Show(state)
        return when (item) {
            MenuItem.Back -> back(state)
            is MenuItem.Category -> {
                val shown = select(state, item)
                MenuStep.Show(shown.copy(category = item.category, genres = emptyList(), selected = MenuItem.All))
            }
            MenuItem.Search -> MenuStep.Show(select(state, item), focusSearchField = true)
            else -> MenuStep.Show(select(state, item))
        }
    }

    /** Back/Menu/Return in the menu: a sub-menu returns to the main menu on its category (the whole of it), else leave. */
    fun back(state: MenuState): MenuStep {
        val category = state.category ?: return MenuStep.Leave
        val item = MenuItem.Category(category)
        return MenuStep.Show(
            state.copy(category = null, genres = emptyList(), selected = item, grid = GridContent.Category(category.id)),
        )
    }

    /**
     * The category answer's sub-genres arrived (ČT's order): an open sub-menu of [slug] lists them. Kept as the
     * first answer gave them (a sub-genre's own answer does not replace them).
     */
    fun withGenres(state: MenuState, slug: String, genres: List<ArchiveGenre>): MenuState {
        if (state.category?.id != slug || state.genres.isNotEmpty()) return state
        return state.copy(genres = genres.distinctBy { it.id })
    }

    /** The categories arrived after the tab opened (or changed): the main menu lists them, the selection stays. */
    fun withCategories(state: MenuState, categories: List<ArchiveCategory>): MenuState = state.copy(categories = categories)

    /** A sort chip; ignored when the grid has no such chip. */
    fun withSort(state: MenuState, sort: ArchiveSort): MenuState =
        if (sort in state.sorts) state.copy(sort = sort) else state

    fun sorts(grid: GridContent): List<ArchiveSort> = when (grid) {
        is GridContent.Category -> listOf(ArchiveSort.POPULAR, ArchiveSort.NEWEST, ArchiveSort.AZ)
        GridContent.Recordings -> listOf(ArchiveSort.NEWEST, ArchiveSort.AZ)
        GridContent.Continue, GridContent.Search -> emptyList()
    }

    fun searchBack(query: String): SearchBack = if (query.isNotEmpty()) SearchBack.CLEAR else SearchBack.MENU

    private fun select(state: MenuState, item: MenuItem): MenuState {
        val grid = gridOf(item, state.category)
        // The sort is kept while the grid shows one category (its sub-genres too), reset for anything else.
        val slug = (grid as? GridContent.Category)?.slug
        val sameCategory = slug != null && slug == (state.grid as? GridContent.Category)?.slug
        val sort = if (grid == state.grid || sameCategory) state.sort else defaultSort(grid)
        return state.copy(selected = item, grid = grid, sort = sort)
    }

    private fun defaultSort(grid: GridContent): ArchiveSort = sorts(grid).firstOrNull() ?: ArchiveSort.POPULAR

    private fun gridOf(item: MenuItem, category: ArchiveCategory?): GridContent = when (item) {
        MenuItem.Search -> GridContent.Search
        MenuItem.Continue -> GridContent.Continue
        MenuItem.Recordings -> GridContent.Recordings
        is MenuItem.Category -> GridContent.Category(item.category.id)
        MenuItem.All, MenuItem.Back -> GridContent.Category(category?.id.orEmpty())
        is MenuItem.Genre -> GridContent.Category(category?.id.orEmpty(), item.genre.id)
    }
}
