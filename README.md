# Rigs of Rods для iOS

[![latest-ipa](https://img.shields.io/github/v/tag/wayl-t/Rigs-of-Rods-for-iOS/latest-ipa?label=latest-ipa&color=blue)](https://github.com/wayl-t/Rigs-of-Rods-for-iOS/releases/tag/latest-ipa)
[![Build](https://img.shields.io/github/actions/workflow/status/wayl-t/Rigs-of-Rods-for-iOS/build-ror-ios.yml?branch=main)](https://github.com/wayl-t/Rigs-of-Rods-for-iOS/actions)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Неофициальный нативный порт [Rigs of Rods](https://www.rigsofrods.org/) — симулятора машин с мягким кузовом — на iPhone и iPad (arm64). Собрано из полного исходного кода игры с iOS-патчами, сенсорным управлением и скачиваемым IPA.

Оригинальная игра и вся слава принадлежат [команде Rigs of Rods](https://www.rigsofrods.org/credits).

> Unofficial native iOS port of the open-source soft-body vehicle simulator, sideloadable as an IPA. English summary below.

---

## Скачать последнюю версию

Актуальная сборка лежит в релизе **latest-ipa** и автоматически перезаписывается каждым успешным прогоном CI:

| Файл | Что это | Ссылка |
| --- | --- | --- |
| **RoR-ios.ipa** | Полная игра, ~115 МБ | [Скачать](https://github.com/wayl-t/Rigs-of-Rods-for-iOS/releases/download/latest-ipa/RoR-ios.ipa) |
| **RoR-ios-lite.ipa** | Облегчённая, ~43 МБ | [Скачать](https://github.com/wayl-t/Rigs-of-Rods-for-iOS/releases/download/latest-ipa/RoR-ios-lite.ipa) |
| Все релизы | История версий | [Releases](https://github.com/wayl-t/Rigs-of-Rods-for-iOS/releases) |

Ставится через сайдлоад: [SideStore](https://sidestore.io/), [Sideloadly](https://sideloadly.io/) или [ESIGN](https://esign.yyyue.xyz/). С обычным бесплатным Apple ID подпись живёт 7 дней, потом её нужно продлить.

## Статус

- Полная игра на устройстве: меню, выбор карты и машины, настройки, вождение
- Сенсорное управление: руль, педали, панель систем машины, свободная камера
- Свои моды через папку content в «Файлах», без пересборки
- Встроенный репозиторий модов: загрузка через NSURLSession, без libcurl
- Сборка и упаковка IPA полностью на бесплатных macOS-раннерах GitHub Actions

## Что сделано

### Управление на экране

- **Руль** — две крупные кнопки слева, газ и тормоз справа, ряд быстрых действий снизу: стояночный тормоз, зажигание, сброс, выход, стартер.
- **Поворотники и аварийка** — отдельная зона над нижним рядом.
- **SYS** — открывает настоящее десктопное верхнее меню игры: Simulation / Tuning / Vehicle AI / Vehicles / Saves / Settings / Tools. Тап открывает меню, кнопка CLOSE его закрывает.
- **VEH** — крупные страницы LIGHTS / DRIVE / EXTRAS для систем машины.
- **SPAWN / REPO / MENU / HUD / TOP** — спавн через штатный селектор, репозиторий модов, главное меню, компактный HUD и скрытие верхней полосы.
- В свободной камере — плавающий джойстик, свайп по сцене для обзора и кнопки высоты.
- Кнопки сделаны светлыми полупрозрачными стёклами с тёмной подписью, чтобы не закрывать 3D-сцену.

### Компактный HUD

Вместо десктопной приборной панели рисуется карточка спидометра:

- скорость в км/ч, текущая передача, RPM и полоска нагрузки двигателя;
- снизу — лампочки дальнего и ближнего света, левого и правого поворотника, аварийки, синхронизированные с реальными огнями машины (DashBoardManager).

### Свободная камера вместо пешего персонажа

Меш игрока на GLES2 не рендерится, поэтому логический объект игрока остаётся для игрового кода, но его графика не регистрируется: вместо аватара включается свободная камера. Выйти из машины можно, джойстик перемещает камеру.

### Свои моды

Положите любой .zip (машина или карта) в «Файлы › На моём iPhone › Rigs of Rods › content». Игра монтирует его автоматически (UIFileSharingEnabled).

### Ключевые исправления по логам устройства

- Чёрное небо → Sandstorm по умолчанию, тени отключены.
- Касания не доходили до меню → мост тач → мышь → ImGui.
- Падение при спавне машин с мягким кузовом → в OGRE исправлен GLES2HardwareBuffer::readData(), буфер привязывается перед glMapBufferRangeEXT.
- Материалы машин уходили в нерабочий Cg-вариант nicemetal → включён gfx_alt_actor_materials.
- Моды из репозитория не ставились → переименование .part → .zip через std::filesystem::rename с фолбэком.

## Как это работает

Репозиторий собирает игру на macOS-раннерах GitHub Actions, без своего Mac и без платного аккаунта Apple Developer:

1. **build-ios-deps.yml** — кросс-компилирует зависимости (OGRE, OIS, MyGUI, AngelScript, SocketW и другие) в один артефакт SDK.
2. **build-ror-ios.yml** — скачивает этот SDK, применяет patches/ror/apply_ios_patches.py к исходникам RoR, собирает arm64, упаковывает бандл, подписывает ad-hoc и публикует IPA в релиз latest-ipa.
3. **patches/verify_ipa.py** — около 25 проверок готового IPA: структура бандла, наличие skeleton.zip, шейдеры MyGUI, RTSS, иконки, сенсорный ввод в бинарнике и критичные CVar-обходы. Сборка падает, если что-то потерялось.

Все правки исходников RoR лежат в одном файле, patches/ror/apply_ios_patches.py, и каждая обёрнута в ifdef ROR_PLATFORM_IOS, поэтому десктопная сборка остаётся нетронутой.

Отдельный workflow build-ipa.yml собирает только приложение-проверку конвейера (тег pipeline-check); игры в нём нет.

## Как установить

1. Откройте ссылку на RoR-ios.ipa в Safari на iPhone или скачайте файл на компьютер и передайте на телефон.
2. Установите через SideStore, Sideloadly или ESIGN, войдя обычным Apple ID.
3. При бесплатном Apple ID продлевайте подпись раз в 7 дней.

> Если страница релиза долго грузится, скачайте IPA из артефактов Actions командой gh run download.

## Диагностика на телефоне

Если игра падает или не рисуется, смотрите «Файлы › На моём iPhone › Rigs of Rods › ror-trace.log»: файл пишется построчно и сохраняет хвост даже при падении. Строки startup:, [fatal], [crash] и cvars after RoR.cfg показывают фазу запуска и причину.

## Ограничения

- Тени не рисуются: PSSM не поддерживается на текущем GLES2-пути.
- Небо только Sandstorm: Caelum и SkyX не собираются.
- Мультиплеер на устройстве не протестирован (SocketW без OpenSSL).
- Сайдлоад с бесплатным Apple ID требует переподписи раз в 7 дней.

## Устройство репозитория

    .github/workflows/
      build-ror-ios.yml         основная сборка: RoR → IPA → Release
      build-ios-deps.yml        iOS-зависимости, переиспользуются
      build-ipa.yml             дымовой тест конвейера, НЕ игра
    patches/
      ror/apply_ios_patches.py  все правки исходников RoR под iOS
      ios/make_app_icon.py      генератор иконки
      verify_ipa.py             проверки готового IPA
    PORT_FINDINGS.md            подробный разбор найденных проблем

## Дорожная карта

- [x] Конвейер Actions → IPA → Release → сайдлоад
- [x] Игра на устройстве: меню, карты, машины, управление
- [x] Свои моды через content и встроенный репозиторий
- [x] Компактный HUD с лампочками света и поворотников
- [x] Светлые полупрозрачные кнопки
- [x] SYS открывает полное десктопное меню
- [ ] Мультиплеер на устройстве (не протестирован)
- [ ] Тени: PSSM на GLES2 или альтернатива
- [ ] Раскладка под iPad

## English summary

Unofficial native iOS (arm64) port of Rigs of Rods, built from the full upstream source on free GitHub Actions macOS runners. Touch controls, a compact HUD with light and indicator lamps, and a sideloadable IPA. Download the latest build from the latest-ipa release and install it with SideStore, Sideloadly, or ESIGN. Rigs of Rods itself is GPLv3 software by the Rigs of Rods Team; this repository only adds the iOS port and build pipeline.

## Credits

- **Original game:** [Rigs of Rods](https://www.rigsofrods.org/) by the [Rigs of Rods Team](https://www.rigsofrods.org/credits), GPLv3.
- **iOS port and build pipeline:** this repository.

## License

Rigs of Rods распространяется под GPLv3, этот порт тоже. Публикация в App Store невозможна из-за лицензии, но сайдлоад разрешён.
