import json, sys
sys.stdout.reconfigure(encoding='utf-8')

batch_options = {
  "890": {"text": "Bir hafta", "shortFeedback": "Garanti veya ortak marka teknik şartnamesindeki kamu düzenine aykırılığın giderilme süresi 1 hafta değil 6 aydır."},
  "891": {"text": "Genel tabirler kapsadığı en dar anlamla resen yorumlanır.", "shortFeedback": "Yönetmelik genel tabirler için en dar anlam yorumunu değil 2 aylık açıklama süresi öngörür; açıklanmadığında bu terimler listeden çıkarılır."},
  "892": {"text": "Muvafakatname tüzel kişiler tarafından da verilebilir.", "shortFeedback": "Tüzel kişilerin muvafakatname verebilmesi mevzuata uygundur; bu ifade doğru olduğu için sorunun aradığı 'yanlış olan' seçeneği değildir."},
  "893": {"text": "Sicile kayıt tarihinden itibaren bir hafta.", "shortFeedback": "Tescil ücreti ödeme süresi sicile kayıt + 1 hafta değil bildirim tarihinden itibaren 2 aydır."},
  "894": {"text": "Bölünme talebi sadece yayıma itiraz aşamasında yapılabilir.", "shortFeedback": "Yönetmelik bölünme talebini itiraz aşamasıyla sınırlamamış; marka tescil edilene kadar bölünme yapılabilir ve bölünmüş başvurular ilk başvurunun tarihini ve rüçhan haklarını korur."},
  "895": {"text": "Kurum tarafından resen yapılır.", "shortFeedback": "Marka hakkından vazgeçme veya marka geri çekme Kurum tarafından resen değil tüm hak sahiplerinin imzasıyla yapılır."},
  "896": {"text": "Beş yıl", "shortFeedback": "Madrid replacement süresi 5 yıl değil iptalden itibaren 3 aydır."},
  "897": {"text": "Altı ay", "shortFeedback": "Marka yayıma itiraz süresi 6 ay değil 2 aydır."},
  "898": {"text": "Markanın benzer ürünlerde tescilli olması durumunda.", "shortFeedback": "Kullanım ispatı talebi benzer ürün tescilliliğine değil itiraza dayanak gösterilen markanın tescil tarihinin üzerinden en az 5 yıl geçmiş olmasına bağlıdır."},
  "899": {"text": "İtiraz incelemesi süresiz olarak askıya alınır.", "shortFeedback": "Süre içinde delil sunulmazsa itiraz incelemesi askıya alınmaz; başka itiraz gerekçesi yoksa itiraz reddedilir."},
  "900": {"text": "Deliller başvuru veya rüçhan tarihinden önceki beş yıllık döneme ait olmalıdır.", "shortFeedback": "Delillerin başvuru veya rüçhan tarihinden önceki 5 yıllık döneme ait olması mevzuata uygundur; bu ifade doğru olduğu için sorunun aradığı 'yanlış olan' seçeneği değildir."},
  "901": {"text": "Sanayi ve Teknoloji Bakanlığı bünyesindeki bir komisyonda.", "shortFeedback": "Kurum kararlarına karşı itiraz Bakanlık komisyonunda değil YİDD bünyesindeki Kurulda incelenir."},
  "902": {"text": "Sınırsız bir süreyle", "shortFeedback": "Arabuluculuk için itiraz incelemesinin ertelenme süresi sınırsız değil 3 aydır."},
  "903": {"text": "Sadece kâr amacı güden işletmeler.", "shortFeedback": "Coğrafi işaret başvurusu kâr amacı güden işletmelerle sınırlı değildir; başvuru hakkı Madde 36'da belirtilen kişi ve kuruluşlara aittir."},
  "904": {"text": "Gümrük tarifesi pozisyon kodlarına göre.", "shortFeedback": "Coğrafi işaret başvurusu ürün grupları gümrük tarife kodlarına değil yönetmelikte sayılan özel kategorilere göre belirtilir."},
  "905": {"text": "Sadece nihai ürünün ortalama satış fiyatı.", "shortFeedback": "Geleneksel ürün adı üretim metodu satış fiyatını değil hammadde özellikleri ve karakteristik üretim tekniklerini içermelidir."},
  "906": {"text": "Patent", "shortFeedback": "Coğrafi sınır belgeleri patent başvurusunda değil yalnızca coğrafi işaret başvurularında zorunlu unsurdur."},
  "907": {"text": "Veri Bankası", "shortFeedback": "Yönetmelik bu kayıt ortamını Veri Bankası değil Sicil olarak adlandırır."},
  "908": {"text": "Sadece Madrid Protokolü hükümleri uygulanır, ulusal yönetmelik dikkate alınmaz.", "shortFeedback": "Markaların uluslararası tescili Türkiye'de yalnızca Madrid Protokolü değil aynı zamanda ilgili Uygulama Yönetmeliği çerçevesinde de yürütülür."},
  "909": {"text": "Sadece patent koruması alabilir, marka olarak tescil edilemez.", "shortFeedback": "Yönetmelik ambalaj biçimlerini yalnızca patentle sınırlamamış; görme duyusu ile algılanabilen ve sicilde gösterilebilen ambalaj biçimleri marka olarak tescil edilebilir."},
  "910": {"text": "Resmi Mühür", "shortFeedback": "Yönetmelik coğrafi işaretler için zorunlu işareti 'Resmi Mühür' olarak değil 'Amblem' olarak öngörmüştür."},
  "911": {"text": "Türkiye'ye bildirildiği günün ortasında", "shortFeedback": "Madrid başvurusu bildirim gününe değil uluslararası başvuru tarihinin ilk saat ve dakikasına bağlıdır."},
  "912": {"text": "Apostille onaylı belgenin sunulması", "shortFeedback": "Latin dışı harf içeren marka örnekleri için apostille belgesi değil harflerin Latin alfabesindeki karşılığı sunulmalıdır."},
  "913": {"text": "Sesin ait olduğu sanatçının resmi sertifikası", "shortFeedback": "Ses markası başvurusunda sanatçı sertifikası değil sesin elektronik ortamda dinlemeye ve saklamaya elverişli kaydı sunulmalıdır."},
  "914": {"text": "20", "shortFeedback": "3D marka açı sayısı 20 değil 6 ile sınırlandırılmıştır."},
  "915": {"text": "Üç yıl", "shortFeedback": "Ortak veya garanti marka teknik şartnamesinin sunulmaması durumunda tanınan süre 3 yıl değil 2 aydır."},
  "916": {"text": "Bu terimler resen en geniş kapsamda kabul edilir.", "shortFeedback": "Yönetmelik açıklanmayan genel tabirleri en geniş kapsamda değil listeden çıkararak değerlendirir."},
  "917": {"text": "Sadece mal/hizmet kalemlerini değiştirme yetkisi", "shortFeedback": "Kısmi yenileme için vekâletnamede özellikle 'kısmi yenileme yetkisi' aranır; mal/hizmet kalemlerini değiştirme yetkisi bu özel yetkinin yerine geçen bir ifade değildir."},
  "918": {"text": "Yargıtay kararı bulunması halinde kabul edilir.", "shortFeedback": "Marka örneği veya mal/hizmet listesinde değişiklik içeren düzeltme talepleri Yargıtay kararı dahil hiçbir koşulda kabul edilmez."},
  "919": {"text": "Bir yıl", "shortFeedback": "Madrid replacement süresi 1 yıl değil iptalden itibaren 3 aydır."},
  "920": {"text": "Bir yıl", "shortFeedback": "Marka yayıma itiraz süresi 1 yıl değil 2 aydır."},
  "921": {"text": "Beş ay", "shortFeedback": "Yabancı dildeki delillerin Türkçe tercüme süresi 5 ay değil 2 aydır."},
  "922": {"text": "Dört ay", "shortFeedback": "Arabuluculuk için itiraz incelemesinin ertelenme süresi 4 ay değil 3 aydır."},
  "923": {"text": "Süt ve süt ürünleri", "shortFeedback": "Süt ve süt ürünleri coğrafi işaret tescilinde yer alan ürün grupları arasındadır; bu nedenle 'biri değildir' iddiası yanlıştır."},
  "924": {"text": "İhracat hedeflenen ülkelerin pazarlama bilgileri", "shortFeedback": "Mahreç işareti başvurusu ihracat pazarlama bilgilerini değil coğrafi alan sınırları içinde yapılacak işlemlerin açıklanmasını şart koşar."},
  "925": {"text": "Kimyasal özellikler", "shortFeedback": "Kimyasal özellikler geleneksel ürün adı 'ürün tanımı' kapsamında zorunlu olarak istenen teknik bilgilerden biridir; bu nedenle 'biri değildir' iddiası yanlıştır."},
  "926": {"text": "Türkiye'de daha önce başvuru yapılmış olan", "shortFeedback": "Madrid Protokolü aynı tarihli başvurularda Türkiye'deki önceki başvuruya değil uluslararası tescil numarası küçük olan başvuruya öncelik tanır."},
  "927": {"text": "Üç hafta", "shortFeedback": "Tescil ücreti eksikliği için tanınan ek süre 3 hafta değil 1 aydır."},
  "928": {"text": "Marka sahibinin adres bilgileri", "shortFeedback": "Marka sahibinin adres bilgileri marka sicilinde yer alan bilgilerdendir; sicilde yer almayan unsur adli sicil kaydıdır."},
  "929": {"text": "Vazgeçme talebi başvuru aşamasında veya tescil sonrası yapılabilir.", "shortFeedback": "Vazgeçmenin hem başvuru hem tescil sonrası aşamada yapılabilmesi mevzuata uygundur; bu ifade doğru olduğu için sorunun aradığı 'yanlış olan' seçeneği değildir."},
  "930": {"text": "Belgenin orijinal hali Bakanlık tarafından çevrilir.", "shortFeedback": "Yönetmelik yabancı dildeki lisans belgesi için Bakanlık çevirisi değil yeminli tercüman onaylı Türkçe tercümesinin eklenmesini şart koşar."}
}

partial_path = r'C:\Users\701693\turk_patent\education\sorular_e_options_partial.json'
with open(partial_path, 'r', encoding='utf-8') as f:
    partial = json.load(f)
before = len(partial)
partial.update(batch_options)
after = len(partial)
with open(partial_path, 'w', encoding='utf-8') as f:
    json.dump(partial, f, ensure_ascii=False, indent=2)
with open(r'C:\Users\701693\turk_patent\education\sorular.json', 'r', encoding='utf-8') as f:
    total = len(json.load(f))
print(f"Batch added: {after - before} | Total: {after}/{total} | Remaining: {total - after}")
