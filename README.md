# Rigs of Rods — iOS

Нативный порт Rigs of Rods на iPhone. Собирается **без Mac и без платного аккаунта
Apple**: сборку делает бесплатный macOS-раннер GitHub Actions, а установку —
SideStore/AltStore с обычным Apple ID.

Подход повторяет то, что сделали авторы GTA V-портов
([kevinerikjs/gta5-ios-port](https://github.com/kevinerikjs/gta5-ios-port),
[c22dev/muguet](https://github.com/c22dev/muguet)) — берём у них проверенную
сборочно-установочную обвязку.

## Текущий статус: веха 0 — проверка конвейера

Задача этой вехи — доказать, что вся цепочка работает на **тривиальном** приложении,
до того как вкладываться в месяцы портирования:

```
GitHub Actions (macOS) -> .ipa -> SideStore/AltStore -> iPhone
```

Приложение показывает на экране:
- версию нативного ядра (компилируется C++20 под arm64);
- реальную силу балки, посчитанную на телефоне по модели RoR;
- **лимит памяти**, который iOS выдаёт приложению — от него зависят графика и пресеты.

Если эти числа видны на iPhone — конвейер работает, и дальше вопрос только в объёме порта.

## Что внутри

```
ror-ios/
├── .github/workflows/build-ipa.yml   # сборка IPA на бесплатном macOS-раннере
├── build_ipa.sh                      # тот же скрипт для запуска на своём Mac
├── app/
│   ├── project.yml                   # описание Xcode-проекта (генератор xcodegen)
│   ├── App/                          # UIKit-оболочка, Info.plist, entitlements
│   └── Source/                       # нативное ядро на C++ (RoRCore)
```

Ключевые решения, скопированные у GTA V-портов, потому что они проверены в бою:

| Приём | Зачем |
|---|---|
| `CODE_SIGNING_ALLOWED=NO` + ad-hoc `codesign --entitlements` | сборка без сертификата; подпись добавляет AltStore/SideStore |
| `App/Free.entitlements` (пустой) и `App/Full.entitlements` | бесплатный Apple ID не умеет доп. entitlements; платный — умеет |
| `UIFileSharingEnabled` + `LSSupportsOpeningDocumentsInPlace` | папка Documents видна в Файлах → **так в приложение попадает контент RoR (машины, карты) без раздувания IPA** |
| `GCSupportsGameMode`, `CADisableMinimumFrameDurationOnPhone` | Game Mode и >60 FPS на ProMotion |
| Ландшафтная ориентация, `UIRequiresFullScreen` | это гоночная игра |

## Как получить приложение на iPhone

### 1. Репозиторий

Репозиторий должен быть **публичным**: GitHub даёт бесплатные macOS-минуты только
публичным. Приватному на бесплатном плане хватит ~8 сборок в месяц
(2000 минут, macOS считается ×10). RoR и так под GPLv3 — скрывать нечего.

```sh
git init
git add .
git commit -m "Milestone 0: pipeline smoke test"
gh repo create ror-ios --public --source=. --push
```

### 2. Собрать

Actions → **Build my IPA** → *Run workflow*. Через ~10 минут в **Releases** появится
`RoRNative.ipa`.

### 3. Установить

Нужен **SideStore** (сайдлоад прямо на телефоне) или **AltStore**.

На Windows для первоначальной настройки AltServer нужны **iTunes и iCloud с сайта
Apple** — версии из Microsoft Store не подходят, в них нет драйверов Apple Mobile Device.

Оба варианта бесплатны, оба переподписывают приложение каждые 7 дней.

### 4. Память (важно)

iOS выдаёт приложению примерно половину RAM. Для софт-боди симулятора этого мало.

- Бесплатный Apple ID → около 3 ГБ.
- Платный ($99/год) → над entitlements из `App/Full.entitlements` → около 4 ГБ.
- Бесплатно повысить лимит можно через **SideStore + [GetMoreRam](https://github.com/hugeBlack/GetMoreRam)**.

Лимит всегда видно на экране приложения.

## Про контент

Моды и контент RoR (гигабайты) **не** кладём в IPA. Варианты:
1. Пользователь копирует папку `content/` в *На моём iPhone › Rigs of Rods* через Файлы или кабелем.
2. Стриминг с компьютера по Tailscale (как в первой версии GTA V-порта).

Благодаря `UIFileSharingEnabled` папка Documents приложения и есть точка входа.

## Дорожная карта

- [x] **Веха 0** — конвейер: Actions → IPA → sideload
- [ ] **Веха 1** — RoR собирается под macOS-десктоп (проверка, что код жив на Apple)
- [ ] **Веха 2** — `physics/` + парсеры `.truck`/`.terrain` вынесены в `rorcore` без OGRE
- [ ] **Веха 3** — запуск и картинка на iPhone (главный нерешённый вопрос: OGRE)
- [ ] **Веха 4** — тач-управление, загрузка машины и карты
- [ ] **Веха 5** — играбельно

## Главный нерешённый вопрос

Сборочно-установочная половина теперь решена. Остаётся портирование: **OGRE на iOS**.
Классический OGRE умеет только GLES2, который Apple пометил устаревшим. Варианты —
Vulkan + MoltenVK или переход на OGRE-Next (там Metal и официальная поддержка iOS).
Объём и обоснование — в `../PORT_TO_IOS.md`.

## Лицензия

Rigs of Rods распространяется под **GPLv3**. Это значит: в App Store приложение
опубликовать нельзя, а сайдлоад на своём телефоне — можно.
