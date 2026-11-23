"""Exact ZombieCash image prompt composition extracted from the private production bot.

Scene lists, visual directions, wardrobe options, camera compositions and branding
constraints are retained verbatim. This module does not call an image provider.
"""
from __future__ import annotations

import random
from bot.scene_history import choose_fresh_scene

SUPPORTED_STYLES = frozenset({"HORROR", "CARTOON", "PHOTO_PINUP"})


def build_image_prompt(style_mode: str) -> tuple[str, str]:
    """Return (complete prompt, selected scene) for one editorial style."""
    if style_mode not in SUPPORTED_STYLES:
        raise ValueError(f"Unsupported ZombieCash image style: {style_mode!r}")

    if style_mode == "HORROR":
        # NIGHTMARE & HORROR: varied, uncompromising live-action outbreak and body horror.
        scenes = [
            "inside a makeshift hospital morgue, a zipped body bag slowly bulges as a grey hand with blackened nails tears through the fabric; rows of cold steel drawers recede into darkness",
            "a deserted subway maintenance room after an outbreak, a wall of infected bodies fused together by dark tendrils, one cloudy eye opening in the wet concrete-like mass",
            "an abandoned field clinic where a zombie medic in a blood-stained surgical gown stands beneath a swinging lamp, holding a cracked specimen jar as something moves inside it",
            "a survivor opens a rusted freezer and finds a frost-covered undead face pressed against the inside of the glass, one milky eye staring directly outward",
            "a flooded underground car park, pale drowned infected rising between half-submerged vehicles, oily water rippling around torn clothing and ruined emergency lights",
            "a narrow apartment stairwell covered in frantic evacuation markings, a motionless infected body hangs upside down from the landing above, its jaw slack and fingers twitching",
            "a ruined supermarket freezer aisle, an emaciated undead creature crouched among spilled frozen food, frost on its decayed skin and a trail of dark footprints leading toward the lens",
            "a derelict church converted into a survivor shelter, pews overturned and candles guttering while a towering infected silhouette slowly unfolds behind the altar",
            "a close forensic-style view of an infected face behind cracked quarantine glass, swollen cloudy eyes, torn cheek prosthetics and a bloody handprint slowly sliding down the pane",
            "a deserted children's hospital corridor at night, abandoned wheelchairs facing a doorway where a grotesquely mutated adult infected figure is half-hidden in the shadows; no children present",
            "a collapsed highway tunnel filled with abandoned cars, a cluster of infected figures clinging unnaturally to the ceiling above a single burning road flare",
            "an industrial slaughterhouse repurposed as an outbreak quarantine zone, hanging plastic curtains stained dark, a huge infected silhouette pressing against them from the other side",
            "a survivor in a gas mask discovers an entire emergency response team turned into motionless infected, still seated around a radio table as the radio crackles",
            "a rain-lashed rooftop helipad, a badly decayed infected emergency pilot drags itself from a crashed rescue helicopter, rotor blades motionless and city fires below",
            "a dark elevator frozen between floors, its doors opening onto a cramped car filled with silent infected staring straight ahead, one slowly turning its head toward camera",
            "an abandoned botanical greenhouse overtaken by black vines, an infected gardener with fungal growths across the face emerges between wet leaves and shattered glass",
            "a ruined underground laboratory, a containment tank split open and a massive malformed infected specimen slumped across the floor beneath pulsing red warning lights",
            "a roadside evacuation bus at dawn, every window fogged from inside and dozens of bloody fingerprints appearing on the glass as a shadow moves between the seats",
            "a deserted motel room with a television flickering static, a blood-smeared bathroom mirror reflecting an infected figure that is not visible in the room itself",
            "a cold storage warehouse, a trail of discarded boots leads to a ceiling-high pile of abandoned protective suits with something breathing underneath",
            "a survivor shines a flashlight into a storm drain and illuminates a nest of pale infected faces packed tightly in the darkness, all looking upward at once",
            "a ruined train platform where an infected conductor stands perfectly still beside an open carriage, dark fluid dripping from one sleeve onto the yellow safety line",
            "a post-outbreak apartment kitchen, an infected mother-like adult figure stands motionless at the sink while dozens of flies circle a spoiled meal and a second silhouette appears in the doorway",
            "a military checkpoint at the edge of a dead city, abandoned hazmat suits lie in a row while one suit slowly rises as if still occupied",
            "a close-up of a survivor's gloved hand discovering a fresh bite mark through a torn sleeve, their flashlight revealing an infected face reflected in a dark window behind them",
            "a ruined underground swimming pool, pale infected bodies suspended beneath still black water, one suddenly pressing both hands against the glass observation wall",
            "a wrecked ambulance interior seen from the rear doors, bloodied medical instruments scattered across the floor and an undead paramedic strapped upright in the driver's seat",
            "an abandoned radio station broadcast booth, a decayed presenter remains seated before a live microphone, headphones fused into matted hair and the ON AIR light still glowing",
            "a forest ranger station surrounded by fog, a line of muddy barefoot prints ends at the ceiling directly above the open doorway",
            "a quarantine checkpoint after a failed evacuation, torn warning tape, overturned barriers and a grotesquely mutated infected figure emerging from a pile of discarded hazmat gear",
            "a close cinematic shot of an undead hand pushing through cracked plaster from inside a sealed wall, dust falling as several more fingers begin to appear",
            "a ruined shopping mall atrium, an infected figure stands on the upper balcony holding a flickering emergency lamp while dozens of silent silhouettes fill the lower level",
            "a hospital laundry room with industrial washers spinning by themselves, one washer door bulging outward as a bloodied hand grips the round glass",
            "a deserted ferry deck in heavy fog, soaked infected passengers stand facing the sea in perfect silence while one turns toward the camera with a split, decayed grin",
            "an abandoned underground bunker, a wall covered in tally marks and desperate warnings, a survivor's flashlight catches a fresh wet handprint appearing behind them",
            "a post-apocalyptic roadside diner, a grotesquely decayed cook stands behind the serving hatch as the kitchen lights flicker and something moves beneath the counter",
            "a ruined science museum after an outbreak, a realistic infected specimen display has broken open and the creature is now standing among the visitors' empty coats",
            "a dark stairwell viewed from above, a lone survivor looks up as a crowd of infected slowly descends every flight without making a sound",
            "a close-up practical-effects portrait of an adult female infected with torn cheek prosthetics, exposed decayed tissue, cracked lips and milky eyes, rain and grime catching the harsh side light",
            "a deserted city bus at blue dawn, an infected passenger sits alone with forehead against the window, leaving a smeared bloody trail as the bus lights switch on"
        ]
        visual_direction = (
            "maximum photorealism: uncompromising live-action outbreak horror, premium practical creature effects and grounded disaster-film cinematography. "
            "Make each image a distinct horror set-piece rather than another generic chase. Rotate between visceral body horror, grotesque undead close-ups, "
            "environmental dread, infection aftermath, claustrophobic discoveries, failed quarantine scenes, mutated creatures and psychological jump-scares. "
            "Show convincing fictional horror details: blood-smeared surfaces, torn and decayed skin prosthetics, bite wounds, bruising, grime, blackened nails, "
            "cloudy eyes, exposed creature makeup, damaged clothing and unsettling physical mutations. Keep gore clearly fictional and cinematic, never instructional. "
            "Use varied adult survivors and undead of different genders, ages (adults only), builds and silhouettes; do not make every scene a beautiful female attacker. "
            "Vary locations, weather, time of day, practical light sources, color temperature and camera distance. Use oppressive atmosphere, strong visual storytelling, "
            "realistic materials, natural anatomy, detailed practical makeup, deep shadows, film grain and high-end horror color grading. "
            "No nudity, sexual violence, children, real victims, text, logos or watermarks. Frightening, shocking and memorable."
        )

        styles = [
            "ultra-photorealistic live-action horror movie still, ARRI Alexa 65 cinema look",
            "photorealistic practical-effects creature feature, anamorphic cinema lens",
            "gritty prestige survival-horror cinematography, natural skin texture and film grain",
            "high-budget zombie thriller frame, realistic stunt choreography and physical lighting",
            "dark cinematic horror photography, documentary-level realism and shallow depth of field",
            "photorealistic night-horror lighting with terrifying close-range creature detail"
        ]
    elif style_mode == "CARTOON":
        scenes = [
            "a glamorous adult gothic zombie countess with violet twin-tails, tiny fangs and a black lace-trimmed corset dress over opaque shorts, dramatically trying to look intimidating while a tiny skeleton valet holds her broken umbrella",
            "a cute chibi vampire-zombie girl in a heart-shaped black beret and striped thigh-high socks guarding the last brain cupcake like a royal treasure, while a raven steals the cherry",
            "a stylish adult undead witch in a plum velvet corset coat and tall boots accidentally summoning a tiny army of dancing skeleton accountants instead of a demon",
            "two adorable gothic zombie girlfriends in elaborate black-and-crimson outfits posing for a dramatic moonlit selfie while a goofy ghost photobombs them with a peace sign",
            "a glamorous adult zombie queen in a jewel-toned gothic gown trying to ride a tiny shopping cart through a ruined mall, crown flying and bats cheering",
            "a cute cartoon zombie pin-up heroine with mint-green hair, black bows and opaque lace stockings serving coffee to exhausted skeleton traders in a pastel gothic cafe",
            "a dramatic adult vampire-zombie heroine in a corset jacket and layered skirt opening a treasure chest that contains one expired coupon and a tiny screaming frog",
            "a pair of stylish adult gothic undead sisters arguing over who gets the last strawberry brain macaron while a skeleton referee consults a ridiculously tiny rulebook",
            "a cute chibi undead girl dressed as a gothic maid polishing a golden crypto chart with a feather duster while the chart immediately falls off the wall",
            "an elegant adult undead geisha in a black-and-jade outfit trying to look mysterious as a tiny ghost keeps tugging her sleeve to ask where the snacks are",
            "a glamorous adult gothic valkyrie zombie posing heroically on a ruined rooftop while her tiny skeleton horse refuses to move until it gets a carrot",
            "three cute gothic zombie girls running a luxury apocalypse beauty salon for skeletons, one applying dramatic eyeliner to a skull with no eyelids",
            "an adult zombie vampire countess in a crimson velvet corset gown hosting a tea party where every guest is a skeleton and the teapot keeps serving bone broth",
            "a chibi undead girl with oversized boots and a black heart hair clip celebrating a tiny green candle while a cucumber wearing a crown steals the spotlight",
            "a stylish adult gothic zombie heroine trying to make a grand entrance through a castle door, only to discover it says PUSH in giant cartoon letters",
            "a cute cartoon witch-zombie in a lace-trimmed black dress brewing a confidence potion that turns every skeleton in the room into an overly enthusiastic motivational speaker",
            "a glamorous adult undead pirate queen in a fitted black corset and red coat dramatically pointing at a treasure map that is actually a takeaway menu",
            "a gothic zombie girl band performing on a ruined stage while the skeleton drummer loses the beat because one of his ribs is being used as a drumstick",
            "a cute chibi zombie princess in a tiny crown negotiating with a raccoon in sunglasses for the return of her stolen wallet",
            "an adult gothic zombie heroine in a stylish black-and-purple outfit trying to scare a trader, but he asks her to review his portfolio first",
            "a tiny zombie in a business suit dramatically mourning a portfolio with a flat line, while a skeleton offers tissues",
            "three goofy zombies fighting over the last cup of coffee in a ruined trading office",
            "a zombie king sitting on a throne made from broken keyboards and proudly wearing a paper crown",
            "a skeleton cashier guarding one can of beans like it is the world's greatest treasure",
            "a cartoon zombie trying to fix a broken Wi-Fi router while tiny ghosts unplug it again",
            "a goofy undead astronaut discovering a moon full of discarded computer mice",
            "a zombie chef cooking brain-shaped pancakes while a raven steals the syrup",
            "a tiny zombie riding a shopping cart through a deserted supermarket, wearing oversized sunglasses",
            "two undead roommates arguing over who sold the last brain-shaped cookie",
            "a cartoon monster bursting out of an ATM, surprised to find only one tiny coin inside",
            "a zombie influencer filming a comeback tutorial while their own arm falls off just outside the frame",
            "a skeleton refreshing a chart so hard the laptop asks for emotional boundaries",
            "a zombie day trader celebrating a green candle that turns out to be a cucumber taped to the screen",
            "a zombie accountant counting beans while a raccoon in sunglasses runs off with the wallet",
            "a dramatic undead hero kicking open a door only to find a queue for the last phone charger",
            "a skeleton explaining diversification to three crows that keep stealing the portfolio",
            "a zombie survivor finally finds a safe bunker, then discovers the password is a CAPTCHA asking it to identify all the zombies",
            "a zombie wedding where the bride throws a bouquet and every undead guest loses an arm reaching for it",
            "an exhausted survivor barricading a door with a sign saying 'Do Not Disturb' while a whole horde politely waits outside",
            "a zombie in a hazmat suit panic-buying toilet paper during the apocalypse while a skeleton calmly takes the last brain-shaped snack",
            "three zombies trying to take a group selfie, but one keeps photobombing as a detached head rolling into frame",
            "a zombie office meeting where everyone is dead inside, and one actual skeleton is the only employee showing enthusiasm",
            "a survivor opens an emergency supply crate and finds 47 charging cables, one sock, and absolutely no food",
            "a zombie trying to scare a survivor, but the survivor is more frightened by the electricity bill pinned to the bunker wall",
            "a cartoon horde queuing patiently at a drive-through window that has a sign saying 'Brains sold out — try the salad'",
            "a zombie trying to enter a seed phrase with one missing finger while the laptop politely says 'try again'",
            "three skeletons holding an emergency DAO meeting to decide who ate the last brain-shaped donut",
            "a zombie proudly showing a green candle to its friends, only for them to reveal it is a cucumber",
            "a survivor opening a legendary loot chest and finding nothing but expired coupons and a single AA battery",
            "a zombie influencer livestreaming a tutorial titled 'How to stay positive' while a meteor lands behind the bunker",
            "two undead traders arguing over a chart while a tiny dog calmly walks away with the only working laptop",
            "a zombie trying to bridge a broken wooden bridge in the apocalypse while holding a crypto wallet and looking confused",
            "a skeleton carefully diversifying its portfolio across three identical cans of beans",
        ]
        visual_direction = (
            "beautiful premium animated comedy art with a gothic-cute fashion identity: expressive adult gothic zombie heroines, "
            "adorable chibi undead sidekicks, stylish skeletons, cheeky ravens and tiny monsters. Make characters visually charming, "
            "memorable and fashion-forward, with elegant silhouettes, expressive eyes, playful fangs, dramatic hair shapes, bows, "
            "black lace details, velvet, corset-inspired fully opaque costumes, striped stockings and jewel-tone accessories. "
            "Keep all human characters unmistakably adult; sensual fashion remains tasteful and fully covered. "
            "Use polished 2D anime-cartoon illustration, premium 3D animation, cel-shaded graphic novel art and stop-motion-inspired "
            "textures in rotation. Beautiful color design: midnight black, plum, lavender, rose pink, teal, mint, crimson and gold; "
            "soft cinematic rim light, rich gradients, crisp linework, appealing shapes and excellent visual hierarchy. "
            "Every scene needs one instantly readable visual gag, expressive reaction and at least one delightful background detail. "
            "Playful spooky atmosphere, cute not childish, sophisticated not generic, no text, no logos."
        )
        styles = [
            "premium 2D gothic anime comedy illustration, elegant linework and expressive faces",
            "high-end 3D animated dark comedy film still, beautiful materials and cinematic lighting",
            "polished cel-shaded gothic graphic novel panel with vivid jewel-tone colors",
            "stylized stop-motion gothic comedy with tactile miniature sets and charming character design",
            "luxury chibi anime sticker-art aesthetic with immaculate silhouettes and expressive reactions",
            "beautiful animated feature-film concept art, rich color scripting and playful visual storytelling",
        ]

    else:
        # UNDEAD AFTER DARK: photorealistic, sensual full-body gothic lingerie fashion portraits.
        scenes = [
            "one unmistakably adult 25+ gothic zombie empress with long raven hair and luminous jade eyes, wearing sheer black floral-lace bra and matching briefs with discreet opaque intimate panels, open crimson silk robe and garter stockings; full-body standing pose in a moonlit Kyoto palace",
            "one adult 25+ vampire countess with glossy dark waves and smoky eyes, wearing a deep-plum translucent lace lingerie set with opaque intimate lining, long lace stockings and an open silk robe; full-length fashion photograph in a rain-soaked palace corridor",
            "one adult 25+ gothic heroine with porcelain-real skin and elegant black hair ornaments, wearing a black-and-crimson sheer lace bra-and-brief set with discreet opaque coverage and flowing silk robe; full-body pose beside a shoji screen",
            "one adult 25+ glamorous undead woman with silver-black hair and ruby eyes, wearing midnight-blue sheer satin-and-lace lingerie with opaque intimate panels, garter belt and open robe; full-length reclining pose against velvet cushions",
            "one adult 25+ curvy vampire duchess with a confident half-smile, wearing delicate black sheer lace lingerie with opaque intimate coverage, patterned thigh-high stockings and a loose crimson silk robe; full-body pose on a stone windowsill",
            "one adult 25+ gothic shrine maiden with long white hair and jade ornaments, wearing emerald sheer-lace lingerie with opaque intimate panels, detached silk sleeves and a flowing translucent robe; full-length pose in a secluded Japanese garden",
            "one adult 25+ dark-romance heroine with warm olive skin and long auburn hair, wearing wine-red translucent lace lingerie with discreet opaque coverage, black lace gloves and a delicate waist chain; full-body pose beside a candlelit staircase",
            "one adult 25+ elegant zombie empress with a naturally full feminine silhouette, wearing ivory sheer lace lingerie with opaque intimate panels and an open deep-crimson silk robe; full-length seated pose at a lacquered dressing table",
            "one adult 25+ gothic vampire woman with midnight-blue hair and amethyst eyes, wearing black mesh-and-lace lingerie with opaque intimate coverage, sheer detached sleeves and thigh-high stockings; full-body pose framed by silk curtains",
            "one adult 25+ undead countess with a poised over-the-shoulder gaze, wearing jade-and-black sheer lace lingerie with discreet opaque coverage and thigh-high stockings; full-length pose on a rain-polished temple veranda",
            "one adult 25+ curvy gothic heroine with natural skin texture and subtle fangs, wearing burgundy sheer-lace lingerie with opaque intimate panels, velvet gloves and a jeweled collar; full-body seated pose in a ruined imperial hall",
            "one adult 25+ elegant vampire hostess with long dark curls and a teasing half-smile, wearing black sheer-lace lingerie with opaque intimate coverage and an open satin robe; full-length pose in a candlelit private salon",
            "one adult 25+ gothic zombie queen with platinum hair and jade-green eyes, wearing deep-emerald sheer lace lingerie with opaque intimate panels, silver chains and a loose fur-trimmed cape; full-body pose before a moonlit pagoda",
            "one adult 25+ dark-fantasy pin-up heroine with naturally curvy proportions, wearing black translucent lace lingerie with opaque intimate coverage, garter stockings and an open silk robe; full-body seated sideways on a velvet chaise",
            "one adult 25+ glamorous undead woman with chestnut hair and realistic skin detail, wearing plum sheer-lace lingerie with discreet opaque intimate lining and gold jewelry; full-length pose beside glowing paper lanterns",
            "one adult 25+ gothic empress with a confident relaxed expression, wearing black-and-jade sheer lingerie with opaque intimate panels and ornate kanzashi hairpins; full-body standing pose in a quiet palace garden",
            "one adult 25+ vampire countess with dark wavy hair and a natural hourglass figure, wearing crimson sheer-lace lingerie with opaque intimate coverage, satin gloves and thigh-high stockings; full-length seated pose in a gothic library",
            "one adult 25+ undead beauty with a short black bob and luminous jade eyes, wearing black mesh lingerie with discreet opaque intimate panels and a deep-red silk robe; full-body pose in a neon-lit Kyoto alley",
            "one adult 25+ gothic heroine with long silver braids, wearing midnight-blue sheer lace lingerie with opaque intimate coverage, antique-silver jewelry and a soft fur stole; full-length pose beside a rain-streaked window",
            "one adult 25+ vampire queen with long raven hair and a knowing half-smile, wearing black sheer-lace lingerie with opaque intimate panels, crimson silk accents and lace stockings; full-body pose beneath warm scarlet lanterns",
            "one unmistakably adult 25+ gothic heroine in an elegant backless black evening gown with a plunging open back, delicate chains across her shoulders and a high slit; photographed in a clear side-profile pose with her face visible in a candlelit palace",
            "one adult 25+ undead pin-up heroine wearing a tasteful black satin bra and high-waisted lace panties with opaque coverage, sheer thigh-high stockings and an oversized open-back silk shirt; relaxed full-body fashion pose in a luxurious ruined suite",
            "one adult 25+ vampire countess in a backless deep-crimson silk dress, bare shoulders and back, long gloves and a dramatic side slit; standing in a clear side-profile pose with her face visible beside a rain-streaked gothic window",
            "one adult 25+ gothic woman in a refined black lace lingerie set with fully opaque bra cups and matching panties, garter belt and stockings; seated sideways on a velvet chaise in a moody editorial pose",
            "one adult 25+ dark-romance heroine in an implied-nude fine-art fashion portrait, her bare back turned to camera, long hair and one arm artfully covering her chest while a dark silk sheet is wrapped securely around her hips; tasteful non-explicit composition in soft window light",
            "one adult 25+ gothic empress in a tasteful implied-nude portrait, seated with her back to camera, one arm and flowing hair covering her chest and a velvet drape covering her hips; elegant sculptural pose, no intimate details visible",
            "one adult 25+ vampire duchess wearing a backless black satin evening outfit with a dramatic open back, delicate shoulder straps and a long flowing skirt; full-length photograph in a ruined opera house",
            "one adult 25+ undead heroine in a black lace bralette and matching high-cut panties with opaque lining, sheer stockings and an open crimson robe slipping off her shoulders; confident fashion-editorial pose in a moonlit suite"
        ]
        visual_direction = (
            "Premium photorealistic live-action UNDEAD AFTER DARK fashion and warrior editorial. Show exactly one unmistakably adult woman age 25+ with realistic human anatomy and natural proportions. Rotate between three clear wardrobe families: (A) gothic lace lingerie with matching briefs, garters, thigh-high stockings and an open silk robe; (B) tasteful implied-nude fine-art styling where the model appears unclothed but breasts and pelvis are fully and securely covered by crossed arms, hands, long hair, a draped silk cloth, cloak, shield or foreground prop; (C) fantasy warrior pin-up styling with ornate anime-inspired armored bras, sculpted metal breastplates, engraved bracers, shoulder guards, short layered battle skirts or armored hip panels, thigh guards and fitted leather details. The armor may be daring and decorative, but it must cover the breasts and intimate areas. Implied nudity is non-explicit: no visible nipples, areolae, genitals or pubic area. "
            "WARRIOR VARIATION: warrior images must show a believable ACTION-READY combat stance, not a static fashion pose. Choose ONE primary weapon that fits the character, culture, outfit and scene; do not automatically repeat sword plus round shield. Rotate distinctly between: Viking axe held low in a guarded stance; long spear or polearm angled forward; single Norse sword in a two-handed guard; short seax dagger held close in a defensive stance; paired short blades; Japanese katana in a controlled draw or ready stance; naginata held diagonally; yumi bow with arrow nocked; throwing spear; or a large war hammer for a heavily armored character. Use a shield only when it makes tactical and visual sense; vary its shape and design, and omit it in most images. Match weapon scale, grip and weight to the model's hands and physique. HAND ANATOMY AND WEAPON CONTACT ARE CRITICAL: show exactly two natural arms and two hands, with five distinct fingers on each visible hand. Every hand holding a weapon must wrap around the handle at a physically plausible grip point; fingers curl around the handle, thumb opposes the fingers, palm contacts the grip, and the handle visibly passes through the closed grip. Both wrists must connect naturally to forearms and align with the direction of force. Never place fingers on a blade edge, guard, empty air or the wrong side of the handle. No extra fingers, missing fingers, fused fingers, duplicated hands, detached wrists, bent-back wrists, merged hand-and-hilt shapes, floating weapons or weapon handles passing through palms. If the selected weapon requires two hands, show both hands clearly separated along the same handle with a believable spacing; if the pose cannot show a correct grip, choose a simpler one-handed guard or lower the weapon. Keep the weapon unobstructed and clearly connected to the hand. Each image should use a different weapon-and-stance combination from recent generations where possible. Mix Norse runic ornament, raven motifs, braided hair, leather, fur, bronze and steel with Japanese lamellar details, lacquer, silk cords and temple styling. "
            "CAMERA AND POSE: for warrior scenes choose a dynamic but physically plausible battle-ready stance that suits the selected weapon: forward or diagonal guard, spear thrust preparation, axe held in a low guard, katana draw, bow-drawing stance, defensive dagger stance, or a grounded two-handed hammer guard. Vary foot placement, torso angle and arm position naturally; do not repeat the same pose. Keep shoulders, ribcage, waist and pelvis aligned without twisting the spine unnaturally. The face must be clearly visible. No extreme over-the-shoulder twist, no back-to-camera pose, no exaggerated spinal arch, no hips thrust toward the lens and no extreme foreshortening. Hands must grip the correct weapon handles with anatomically correct fingers and wrists. Keep both hands fully inside the frame and large enough to inspect; do not crop hands or hide the weapon grip behind the shield, hair, clothing or body. "
            "Use an eye-level 50mm fashion-photo perspective, moderate camera distance and a full-length or three-quarter composition. Avoid wide-angle distortion and extreme close-ups. Keep the head, torso, arms, hands, legs and feet proportionate and fully readable. "
            "EASTERN + VIKING VISUAL IDENTITY: blend Japanese and broader East Asian imperial atmosphere with Nordic Viking shieldmaiden heritage. Use rain-dark Japanese temples, pagoda bridges, shoji screens and lacquered palaces alongside carved Norse longship motifs, subtle runes, knotwork, raven emblems, aged bronze, forged silver, braided leather and fur trim. Palette: deep jade, charcoal, midnight blue, antique gold, oxblood, muted crimson, cold steel and warm lantern amber. "
            "Photographic realism is mandatory: natural skin texture, believable face, realistic hands with five fingers, anatomically correct shoulders, elbows, hips, knees and feet, physically plausible armor, cloth and metal reflections. No distorted anatomy, twisted torso, duplicated limbs, fused fingers, malformed hands, exaggerated body parts, plastic skin, CGI, cartoon rendering, text, logos or watermarks. Anime-inspired costume design is allowed, but the finished image must look like a real live-action photograph. "
            "Maintain a bold, elegant, sensual warrior-editorial mood. In implied-nude looks, coverage by hands, hair, cloth, cloak, shield or props must be complete and unmistakable."
        )
        styles = [
            "ultra-photorealistic full-body gothic lingerie editorial, medium-format camera, natural skin texture and fine lace detail",
            "luxury sheer-lace dark fashion campaign, crisp 85mm full-length photograph, realistic fabric transparency",
            "prestige live-action dark-romance film still, full-body composition, practical candlelight and lifelike skin",
            "high-end post-apocalyptic gothic lingerie campaign, cinematic 50mm full-length photograph and natural depth of field",
            "photorealistic vampire pin-up fashion photography, translucent black lace lingerie and restrained jade-green rim light",
            "luxury dark editorial full-body photograph, realistic eyes, natural imperfections, fine film grain and elegant chiaroscuro"
        ]
    wardrobe_direction = ""
    if style_mode == "PHOTO_PINUP":
        # Rotate lingerie, tasteful fully covered implied-nude fine art, and fantasy warrior armor looks.
        wardrobe_direction = random.choice([
            "Look 1 — black floral-lace bra and matching high-waisted panties with opaque lining, garter belt, sheer stockings and open silk robe",
            "Look 2 — deep-jade lace bralette and matching panties with fully opaque intimate panels, delicate side straps and thigh-high stockings",
            "Look 3 — elegant black backless fashion top with a deep open back, thin jeweled straps and a high slit; face visible in a natural three-quarter pose",
            "Look 4 — burgundy satin open-back gown with bare shoulders and long gloves, photographed from a natural side angle with face clearly visible",
            "Look 5 — black lace lingerie set with opaque cups and matching panties, garter straps and long gloves; tasteful full-body editorial",
            "Look 6 — tasteful implied-nude fine-art portrait: adult model's breasts completely covered by crossed arms and long hair, pelvis fully covered by a draped silk cloth; elegant, non-explicit",
            "Look 7 — implied-nude warrior bath scene: shoulders and back bare, chest concealed behind a raised round shield and long hair, hips fully covered by folded linen; no intimate details visible",
            "Look 8 — ornate black-and-antique-gold anime-inspired armored bra with engraved metal cups, fitted leather bracers, armored hip panels and thigh guards; photorealistic live-action costume",
            "Look 9 — Viking axe-maiden: dark-steel anime-inspired armored bra, fur-edged leather shoulder mantle, engraved belt, layered battle skirt and thigh guards; gripping one broad-bearded axe in a low diagonal guard, no shield",
            "Look 10 — East-Asian/Norse spear warrior: jade-lacquered armored bra, bronze lamellar panels, silk cords and bracers; holding a long yari spear in a forward diagonal guard, feet in a grounded stance",
            "Look 11 — raven berserker: fitted black leather and sculpted protective chest armor, braided hair and fur accents; wielding a heavy two-handed war hammer in a stable ready stance, no shield",
            "Look 12 — katana huntress: ornate black-and-jade armored bra, engraved arm guards, protective hip armor and thigh-high boots; one hand on the katana hilt in a controlled half-draw stance, no shield",
            "Look 13 — Nordic seax fighter: dark bronze scale armor bra, leather skirt panels and bracers; holding a short seax dagger close in a compact defensive stance, no shield",
            "Look 14 — Japanese-Norse archer: fitted lacquered chest armor, layered leather and silk, bracers and protective hip panels; drawing a traditional yumi bow with a nocked arrow, correct archery posture",
            "Look 15 — dual-blade shieldmaiden: ornate steel-and-leather armored bra, fur shoulder trim and layered battle skirt; holding two short seax blades in a balanced crossed guard, no shield",
            "Look 16 — naginata warrior: jade and antique-gold armored bra, lamellar hip guards and engraved bracers; holding a long naginata diagonally with both hands in a poised combat stance, no shield",
            "Look 13 — elegant black satin open-back top with lace briefs with opaque coverage and thigh-high stockings",
            "Look 14 — ivory lace bra and matching high-cut panties with opaque lining, sheer stockings and a loose silk robe"
        ])

    if style_mode == "HORROR":
        composition = random.choice([
            "extreme close-up creature reveal with a second horror detail reflected in glass behind it",
            "wide establishing shot of an abandoned outbreak site, tiny human silhouette dwarfed by the environment",
            "overhead surveillance-camera angle showing a disturbing pattern of bodies and one figure out of place",
            "claustrophobic first-person flashlight view into a sealed room",
            "symmetrical centered composition in a long institutional corridor with one impossible detail at the far end",
            "low-angle shot looking up from floor level at a towering mutated infected",
            "forensic evidence-style close shot of a contaminated object with the infected threat softly visible behind",
            "silhouette framed by a doorway, backlit by a failing emergency lamp",
            "reflection shot in a cracked mirror where the reflected threat does not match the room",
            "long-lens view through rain-streaked glass, compressed layers of abandoned vehicles and distant infected",
            "macro practical-effects portrait focused on decayed skin, cloudy eye and torn creature prosthetics",
            "wide anamorphic cinema frame with oppressive negative space and a single infected figure",
        ])
    else:
        composition = random.choice([
            "full-body 50mm fashion photograph, complete figure head-to-toe, natural standing pose, face and outfit in crisp focus",
            "full-body portrait beneath scarlet lanterns, relaxed confident pose, complete legs and feet visible",
            "full-body seated fashion portrait on a velvet chaise, legs and silhouette visible, relaxed natural posture",
            "full-body side-profile pose beside a moonlit shoji screen, face clearly visible, complete silhouette and legs visible",
            "full-body seated portrait at a lacquered dressing table, legs visible, realistic anatomy",
            "full-body editorial photograph framed by silk curtains, subtle half-smile, natural skin texture and shallow depth of field",
            "full-length fashion photograph with arms relaxed and clearly separated from the torso, legs and feet fully in frame",
            "full-body portrait beside a rain-streaked palace window, relaxed hip shift, complete figure visible",
            "full-body standing pose, one hand resting lightly at the waist, the other arm relaxed, complete figure head-to-toe",
            "full-body seated sideways pose with both legs visible, relaxed shoulders and a face clearly visible in a left-or-right side profile",
            "full-body photograph in a candlelit gothic salon, sheer lingerie styling, realistic eyes and soft cinematic light",
            "centered full-body photograph with a clean background, natural proportions, legs and feet visible, confident expression"
        ])
    chosen_scene = choose_fresh_scene(style_mode, scenes)
    prompt = (
        "Create a completely new original ZombieCash zombie-apocalypse image. "
        "Scene: " + chosen_scene + ". Visual direction: " + visual_direction + ". "
        "Art style: " + random.choice(styles) + ". Camera: " + composition + ". "
        "Make this image visually distinct from recent posts; vary location, character appearance, body type, hairstyle, outfit, pose, camera distance, lens perspective, color palette, and lighting. "
        "Use a different visual subgenre and composition each time; avoid repeated poster layouts and repeated character designs. "
        "CAMERA ORIENTATION FOR UNDEAD AFTER DARK IS CRITICAL: show a natural LEFT OR RIGHT SIDE PROFILE. Keep shoulders, ribcage, waist and pelvis aligned in the same direction; do not twist the torso or rotate the hips toward the camera. The model's body is side-on, while her head turns only slightly toward the lens so her face remains visible. Never show a rear view, back-to-camera pose, looking-away pose or over-the-shoulder rear composition. Use an eye-level 50mm perspective with natural proportions and no wide-angle distortion. " +
        "ANATOMY QUALITY CONTROL IS CRITICAL: every visible human character has exactly one head, one neck, two shoulders, two arms, two hands, two legs and two feet; natural human proportions, correctly attached limbs, anatomically plausible joints and hands, five fingers on each visible hand. Never add extra, duplicated, missing, fused, twisted or detached limbs, hands, feet, fingers, heads or facial features. No backwards-facing heads, reversed joints, broken spines, impossible body twists, melted anatomy or overlapping body parts. Keep hands and feet clearly separated and naturally posed. Avoid extreme foreshortening, hands directly against the lens, limbs crossing the torso, crawling on all fours, mid-air contortions and complex multi-character contact. Prefer stable, physically believable standing, crouching, kneeling, walking or running poses with clear silhouettes. If a pose risks anatomical distortion, simplify it rather than sacrificing anatomy. "
        "Square composition, high detail, believable materials, strong readable focal point. "
        "For HORROR/NIGHTMARE, create maximum photorealism and rotate between beautiful adult (25+) female zombie predators and adult female survivors. Female undead should dominate the frame in readable, physically plausible actions: stalking, hiding in ambush, rising, walking, running or reaching from a natural stance. Other scenes show adult women escaping pursuit with clear, grounded running poses. Keep the main character large in frame and avoid crowded physical interactions. Prioritize practical creature makeup, decayed skin, natural pores, anatomically correct bodies and hands, believable stunt movement, fabric physics, cinematic lenses, film grain and dramatic lighting. Torn survival outfits may look daring but must keep intimate areas fully covered; no nudity or sexual activity. "
        "For PHOTO_PINUP/UNDEAD AFTER DARK, create an unmistakably photorealistic live-action adult gothic pin-up fashion photograph, not anime or illustration. The heroine is 25+ and must look like a naturally beautiful real adult woman, with naturally full feminine curves, a defined waist, beautiful hips and a full bust in realistic, non-caricatured proportions. " +
        "Apply this exact wardrobe brief: elegant black, deep-jade or oxblood lace lingerie, delicate bra-and-brief sets with opaque intimate panels, fine garters, suspender details, open silk robes and thigh-high stockings. Keep the established sensual gothic pin-up style. No bulky armor, corsets, basques, bodysuits, dresses, skirts or shorts. Keep intimate areas covered by opaque fabric. " +
        "Wardrobe variation for this image: " + wardrobe_direction + ". " +
        "Make the image sensual, seductive and confident through natural posture, believable weight, relaxed gestures, expressive eyes and genuine facial emotion. Use lifelike skin pores, subtle natural imperfections, realistic hair strands and minimal retouching; avoid plastic skin and artificial beauty-filter effects. " +
        "The ZombieCash world should be visible in the atmosphere: post-apocalyptic gothic luxury, dark refined interiors, charcoal grey, black, muted jade green and restrained crimson accents. Most images should use beautiful glamorous gothic fashion with natural skin and elegant makeup; occasionally use the original stronger undead look as a deliberate variation: pale porcelain corpse-like complexion, shadowed eerie eyes, subtle undead makeup and a haunting but beautiful expression, like a cinematic vampire-zombie fashion portrait. Keep this variation tasteful and editorial, not graphic. Photograph it like a luxury dark editorial with detailed fabrics and stitching, cinematic light, true-to-life light falloff, optical depth of field and restrained film grain. No nudity or sexual activity, no anime, CGI, exaggerated proportions, text, logos or watermarks. "
        "ZombieCash branding must never be misspelled. If readable lettering appears on the garment, it must be exactly '$ZOMBCASH' in one clean, straight, high-contrast line, spelled Z-O-M-B-C-A-S-H with the dollar sign before it. No extra words or fake lettering. If exact text is uncertain, leave the garment blank rather than generating gibberish. "
        "Keep the horror-creature look exclusive to NIGHTMARE, but allow UNDEAD AFTER DARK to occasionally use a more visibly undead, haunting vampire-zombie fashion look as one randomly selected visual option. Most posts remain glamorous gothic fashion; some may feature the original eerie undead styling with pale skin, shadowed eyes and subtle corpse-like makeup. Fuse East Asian gothic fashion with tasteful Norse/Viking details such as Japanese temple architecture, pagoda bridges, shoji screens, subtle runes, raven motifs, Nordic knotwork and aged bronze. Keep the heroine visually compelling, with expressive face and elegant styling. Avoid graphic gore or extreme disfigurement. Keep the heroine large enough for her face and outfit to read clearly. Prioritize natural pose, visible face, simple stable anatomy and realistic proportions. No rear-view pose, no twisted torso, no distorted anatomy, no anime, cartoon, CGI-plastic skin, watermarks or unrelated logos."
    )

return prompt, chosen_scene
