-- =============================================================================
-- Locarno top-level explanatory notes — Turkish translations
-- =============================================================================
-- Source: WIPO Locarno Classification, 15th Edition (in force 2025-01-01).
--   Authentic English text: locpub.wipo.int (List of Classes and Subclasses
--   with Explanatory Notes, top-level "Note(s)" sections only).
-- Translation: English → Turkish, preserving "Sınıf N-NN" cross-references.
-- Coverage: Only the 15 classes that have a top-level explanatory note in the
--   WIPO source are populated here. The remaining 17 classes (03, 04, 09, 11,
--   14, 15, 18, 19, 20, 21, 22, 23, 25, 26, 27, 29, 32) carry no top-level
--   note in the WIPO source and are intentionally left NULL.
--
-- Rationale: feeds richer per-class context to the Qwen/Gemini Locarno class
--   suggester (services/locarno_suggest_service.py:_build_prompt), mirroring
--   the Marka/Nice prompt that already includes per-class descriptions.
--
-- Idempotent: re-running the file is safe (UPDATE by primary key).
-- Rollback:   UPDATE locarno_classes_lookup SET description = NULL;
-- =============================================================================

UPDATE locarno_classes_lookup SET description = $$İnsanlar için gıda maddeleri, hayvanlar için gıda maddeleri ve diyetetik gıdaları kapsar. Ambalajları kapsamaz (Sınıf 9).$$ WHERE class_number = '01';

UPDATE locarno_classes_lookup SET description = $$Bebek giysilerini (Sınıf 21-01), yangın tehlikelerine karşı koruma, kaza önleme ve kurtarma için özel ekipmanları (Sınıf 29) ve hayvan giysilerini (Sınıf 30-01) kapsamaz.$$ WHERE class_number = '02';

UPDATE locarno_classes_lookup SET description = $$Metresi ile satılan ve hazır mamul olmayan tüm tekstil veya benzeri ürünleri kapsar. Hazır ürünleri kapsamaz (Sınıf 2 veya Sınıf 6).$$ WHERE class_number = '05';

UPDATE locarno_classes_lookup SET description = $$Birden fazla alt sınıfta yer alan bileşenleri içeren bileşik mobilya ürünleri Sınıf 06-05'te sınıflandırılır. Tek bir tasarım olarak değerlendirilebilen mobilya takımları Sınıf 06-05'te sınıflandırılır. Tekstil parça mallarını kapsamaz (Sınıf 5).$$ WHERE class_number = '06';

UPDATE locarno_classes_lookup SET description = $$Motor tahrikli olsalar bile el ile çalıştırılan ev aletlerini ve gereçlerini kapsar. Yiyecek ve içecek hazırlama makineleri ile cihazlarını kapsamaz (Sınıf 31).$$ WHERE class_number = '07';

UPDATE locarno_classes_lookup SET description = $$Mekanik güç kas kuvvetinin yerini alsa bile el ile çalıştırılan aletleri kapsar; örneğin elektrikli testereler ve matkaplar. Makineleri veya takım tezgâhlarını kapsamaz (Sınıf 15 veya Sınıf 31).$$ WHERE class_number = '08';

UPDATE locarno_classes_lookup SET description = $$Elektrikle çalışan cihazları da kapsar.$$ WHERE class_number = '10';

UPDATE locarno_classes_lookup SET description = $$Tüm taşıtları kapsar: kara, deniz, hava, uzay ve diğerleri. Yalnızca bir taşıtla bağlantılı olarak var olan ve başka bir sınıfa konulamayan parçaları, bileşenleri ve aksesuarları da kapsar; taşıtlara ait bu parçalar, bileşenler ve aksesuarlar ilgili taşıtın alt sınıfında, ya da farklı alt sınıflardaki birden çok taşıt için ortaksa Sınıf 12-16'da yer alır. İlke olarak, başka bir sınıfa konulabilen taşıt parçalarını, bileşenlerini ve aksesuarlarını kapsamaz; bu parçalar, bileşenler ve aksesuarlar aynı türden, yani aynı işleve sahip ürünlerle aynı sınıfa konulur. Örneğin otomobil halıları veya paspasları halılarla birlikte sınıflandırılır (Sınıf 06-11); taşıtlar için elektrik motorları Sınıf 13-01'de, elektriksiz motorlar Sınıf 15-01'de yer alır (motorların bileşenleri için de aynı kural geçerlidir); otomobil farları aydınlatma cihazlarıyla birlikte sınıflandırılır (Sınıf 26-06). Taşıt maketlerini kapsamaz (Sınıf 21-01).$$ WHERE class_number = '12';

UPDATE locarno_classes_lookup SET description = $$Yalnızca elektrik akımı üreten, dağıtan veya dönüştüren cihazları kapsar. Bununla birlikte elektrik motorlarını da kapsar. Elektrikli saatler (Sınıf 10-02) gibi elektrikle çalışan cihazları veya elektrik akımı ölçüm cihazlarını (Sınıf 10-04) kapsamaz.$$ WHERE class_number = '13';

UPDATE locarno_classes_lookup SET description = $$Fotoğrafçılık veya sinematografi lambalarını kapsamaz (Sınıf 26-05).$$ WHERE class_number = '16';

UPDATE locarno_classes_lookup SET description = $$Müzik aleti kılıflarını (Sınıf 03-01) veya ses kayıt ve yeniden üretim ekipmanlarını (Sınıf 14-01) kapsamaz.$$ WHERE class_number = '17';

UPDATE locarno_classes_lookup SET description = $$"Tıbbi ekipman" terimi cerrahi, diş hekimliği ve veterinerlik ekipmanlarını da kapsar.$$ WHERE class_number = '24';

UPDATE locarno_classes_lookup SET description = $$Ambalajları kapsamaz (Sınıf 9).$$ WHERE class_number = '28';

UPDATE locarno_classes_lookup SET description = $$Hayvan gıdalarını (Sınıf 1) veya hayvanlar için eczacılık ve kozmetik ürünlerini (Sınıf 28-01 veya Sınıf 28-02) kapsamaz.$$ WHERE class_number = '30';

UPDATE locarno_classes_lookup SET description = $$Yiyecek veya içecek servisi ya da hazırlığı için el ile çalıştırılan gereçleri, aletleri ve cihazları (Sınıf 7) veya mutfak bıçakları ve kemik ayırma bıçaklarını (Sınıf 08-03) kapsamaz.$$ WHERE class_number = '31';
