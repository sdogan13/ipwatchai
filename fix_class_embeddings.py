"""Re-embed nice_classes_lookup with comprehensive bilingual keyword summaries."""
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
load_dotenv()

# Comprehensive bilingual keyword summaries - Turkish-first since most users search in TR
# These are accurate descriptions of what each class covers, compensating for broken DB descriptions
CLASS_SUMMARIES = {
    1: "Kimyasallar, sanayi kimyasallari, tarim kimyasallari, gubreler, yapistiriclar, recine, plastik hammadde. Chemicals, industrial chemicals, fertilizers, adhesives, resin, raw plastics.",
    2: "Boyalar, vernikler, laklar, pas koruyucu, boya maddeleri, metal toz ve yapraklar. Paints, varnishes, lacquers, anti-rust, coatings, dyes, pigments.",
    3: "Kozmetik, parfum, esansiyel yaglar, sabun, temizlik maddeleri, dis macunu, deodorant, cilt bakim, makyaj, sac losyonu. Cosmetics, perfume, essential oils, soap, cleaning, toothpaste, deodorant, skincare, makeup, hair lotion.",
    4: "Sinai yaglar, gres yagi, yaglayicilar, yakitlar, motor yakiti, benzin, mazot, aydinlatma maddeleri, mum. Industrial oils, grease, lubricants, fuels, motor fuel, petrol, diesel, candles.",
    5: "Ilaclar, ilac, eczane, eczacilik, eczacilik urunleri, tibbi kimyasallar, veteriner ilaclar, diyet takviyeleri, vitamin, bebek mamasi, dezenfektan, bocek ilaci, aspirin, antibiyotik, agri kesici. Pharmaceuticals, pharmacy, drugs, medicine, medical chemicals, veterinary, dietary supplements, vitamins, baby food, disinfectants, pesticides.",
    6: "Adi metaller, metal alasimlar, metal yapi malzemeleri, demir, celik, aluminyum, bakir, metal borular, hirdavat, metal konteynerler, kasalar. Common metals, metal alloys, metal building materials, iron, steel, aluminum, copper, metal pipes, hardware, metal containers, safes.",
    7: "Makineler, takim tezgahlari, motorlar, makine parcalari, tarim makineleri, sanayi makineleri, kulucka makinesi, otomat. Machines, machine tools, motors, engines, machine parts, agricultural machinery, industrial machines, incubators, vending machines.",
    8: "El aletleri, elle calisan aletler, bicak, catal, kasik, tiras bicagi, makas, tornavida, pense, cekic. Hand tools, manual tools, cutlery, knives, forks, spoons, razors, scissors, screwdrivers, pliers, hammers.",
    9: "Elektronik, elektronik cihaz, bilgisayar, bilgisayar donanimi, bilgisayar yazilimi, yazilim, uygulama, mobil cihaz, telefon, akilli telefon, tablet, kamera, olcme aleti, sinyal cihazi, veri isleme, DVD, CD, dijital kayit, yazar kasa, hesap makinesi, yangin sondurme cihazi. Electronics, electronic devices, computers, computer hardware, computer software, software, applications, apps, mobile devices, smartphones, phones, tablets, cameras, measuring instruments, signaling, data processing, fire extinguishers, calculators.",
    10: "Tibbi cihazlar, cerrahi aletler, dis hekimligi cihazlari, kateter, diyaliz, protez, ortopedik araclar, tibbi aletler. Medical devices, surgical instruments, dental devices, catheters, dialysis, prosthetics, orthopedic devices, medical apparatus.",
    11: "Aydinlatma, isitma, sogutma, klima, havalandirma, kombi, kalorifer, soba, firin, buzdolabi, dondurucu, su aritma, kurutma makinesi, sac kurutma, musluk, dus, klozet, banyo. Lighting, heating, cooling, air conditioning, ventilation, boilers, radiators, stoves, ovens, refrigerators, freezers, water purification, dryers, hair dryers, faucets, showers, toilets, bathroom fixtures.",
    12: "Tasitlar, otomobil, araba, motosiklet, bisiklet, kamyon, otobus, traktor, romork, arac parcalari, lastik, motor, sanziman, fren, amortisor, arac koltugu, dikiz aynasi. Vehicles, automobiles, cars, motorcycles, bicycles, trucks, buses, tractors, trailers, vehicle parts, tires, engines, transmissions, brakes, shock absorbers, car seats, mirrors.",
    13: "Atesli silahlar, havali silahlar, tufek, tabanca, muhimmat, mermi, patlayici, fisek, barit, piroteknik. Firearms, air guns, rifles, pistols, ammunition, bullets, explosives, cartridges, gunpowder, pyrotechnics.",
    14: "Mucevherat, kuyumculuk, altin, gumus, kiymetli taslar, yuzuk, kolye, bilezik, kupe, saat, kol saati, duvar saati, anahtarlik, kol dugmesi, kravat ignesi. Jewelry, goldsmithing, gold, silver, precious stones, rings, necklaces, bracelets, earrings, watches, wristwatches, wall clocks, keychains, cufflinks, tie pins.",
    15: "Muzik aletleri, gitar, piyano, keman, davul, flut, saz, baglama, ud, org, muzik aksesuarlari. Musical instruments, guitar, piano, violin, drums, flute, saz, baglama, oud, organ, music accessories.",
    16: "Kagit, karton, ambalaj, matbaa, basili yayinlar, kitap, dergi, gazete, kirtasiye, kalem, silgi, bant, defter, takvim, poster, fotograf, ofis malzemeleri, cizim malzemeleri. Paper, cardboard, packaging, printing, publications, books, magazines, newspapers, stationery, pens, erasers, tape, notebooks, calendars, posters, photographs, office supplies, drawing materials.",
    17: "Kaucuk, plastik, yalitim malzemeleri, plastik film, plastik levha, polipropilen, lamine malzeme, dekoratif plastik, conta, hortum. Rubber, plastic, insulation materials, plastic film, plastic sheets, polypropylene, laminate materials, decorative plastic, gaskets, hoses.",
    18: "Deri, deri urunleri, canta, el cantasi, sirt cantasi, bavul, valiz, cuzdan, semsiye, baston, tasma, kosum takimi, eyer. Leather, leather goods, bags, handbags, backpacks, suitcases, luggage, wallets, umbrellas, walking sticks, leashes, harnesses, saddles.",
    19: "Yapi malzemeleri, insaat malzemeleri, beton, cimento, kiric, alci, mermer, tugla, seramik, karo, cam, prefabrik yapi, metalden olmayan yapi elemanlari, kapi, pencere, cati malzemesi, asfalt, kum, cakil. Building materials, construction materials, concrete, cement, lime, plaster, marble, bricks, ceramics, tiles, glass, prefabricated buildings, doors, windows, roofing, asphalt, sand, gravel.",
    20: "Mobilya, masa, sandalye, koltuk, dolap, yatak, silte, yastik, ayna, cerceve, bebek besigi, yurutec, ahsap dekorasyon, perde cubugu. Furniture, tables, chairs, armchairs, cabinets, beds, mattresses, pillows, mirrors, frames, cribs, walkers, wooden decorations, curtain rods.",
    21: "Ev gerecleri, mutfak gerecleri, tabak, bardak, tencere, tava, porselen, seramik, cam esya, firca, supurge, paspas, dis fircasi, tarak, kap kacak, sise acacagi, utu masasi, temizlik gerecleri. Household utensils, kitchenware, plates, glasses, pots, pans, porcelain, ceramics, glassware, brushes, brooms, mops, toothbrushes, combs, cookware, bottle openers, ironing boards, cleaning utensils.",
    22: "Halatlar, ipler, aglar, balik aglari, cadir, tente, branda, yelken, arac ortusu, doldurma malzemeleri, elyaflar, sera ortusu. Ropes, cords, nets, fishing nets, tents, awnings, tarpaulins, sails, vehicle covers, stuffing materials, fibers, greenhouse covers.",
    23: "Iplikler, tekstil iplikleri, dikis ipligi, orgu ipligi, nakis ipligi, pamuk iplik, yun iplik, ipek iplik, sentetik iplik. Yarns, threads, textile yarns, sewing thread, knitting yarn, embroidery thread, cotton yarn, wool yarn, silk yarn, synthetic yarn.",
    24: "Kumaslar, tekstil urunleri, perde, yatak ortusu, nevresim, carsaf, yastik kilifi, battaniye, yorgan, havlu, bayrak, uyku tulumu. Textiles, fabrics, curtains, bedspreads, duvet covers, sheets, pillowcases, blankets, quilts, towels, flags, sleeping bags.",
    25: "Giyim, kiyafet, elbise, gomlek, pantolon, etek, ceket, mont, kazak, tisort, ic giyim, corap, ayakkabi, bot, terlik, sandalet, spor ayakkabi, sapka, bere, kasket, kemer, fular, sal, moda, giysi, bornoz, pijama, mayo, bikini. Clothing, garments, dresses, shirts, trousers, skirts, jackets, coats, sweaters, t-shirts, underwear, socks, shoes, boots, slippers, sandals, sneakers, hats, berets, caps, belts, scarves, shawls, fashion, footwear, swimwear.",
    26: "Tuhafiye, dantel, nakis, dugme, fermuar, igne, dikis ignesi, orgu ignesi, toka, broslar, kurdele, serit, sac tokalari. Haberdashery, lace, embroidery, buttons, zippers, needles, sewing needles, knitting needles, buckles, brooches, ribbons, bands, hair clips.",
    27: "Hali, kilim, paspas, yer kaplama, halilarin uretimi, linoleum, yapay cim, duvar kaplama, zemin doseme, hali yikama. Carpets, rugs, mats, floor coverings, linoleum, artificial turf, wall hangings, flooring.",
    28: "Oyuncaklar, oyunlar, spor malzemeleri, video oyunlari, jimnastik aletleri, olta takimi, balik yemi, avcilik malzemeleri, yilbasi susleri, parti malzemeleri. Toys, games, sporting goods, video games, gymnastics equipment, fishing tackle, bait, hunting equipment, Christmas decorations, party supplies.",
    29: "Et urunleri, balik, kumes hayvanlari, islenmus et, sucuk, salam, sosis, pastirma, sut, peynir, yogurt, tereyagi, yumurta, zeytin, bakliyat, konserve, dondurulmus gida, meyve sebze isleme, corba, cips, kuruyemis, tahin. Meat, fish, poultry, processed meat, sausages, salami, dairy, milk, cheese, yogurt, butter, eggs, olives, legumes, canned food, frozen food, processed fruits and vegetables, soup, chips, nuts, tahini.",
    30: "Gida, ekmek, makarna, un, pirinc, seker, cay, kahve, kakao, cikolata, bal, baharat, sos, sirke, maya, simit, pogaca, pide, borek, pasta, baklava, tatlilar, dondurma, hububat, musli. Food, bread, pasta, flour, rice, sugar, tea, coffee, cocoa, chocolate, honey, spices, sauce, vinegar, yeast, pastry, bakery products, desserts, ice cream, cereals.",
    31: "Tarim urunleri, taze meyve, taze sebze, tohum, fide, canli bitkiler, cicekler, hayvan yemi, malt, canli hayvanlar, kulucka yumurtasi, ormancilik urunleri, kedi kumu. Agricultural products, fresh fruits, fresh vegetables, seeds, seedlings, live plants, flowers, animal feed, malt, live animals, hatching eggs, forestry products, cat litter.",
    32: "Bira, alkolsuz icecekler, maden suyu, kaynak suyu, soda, meyve suyu, portakal suyu, mesubat, gazli icecek, enerji icecegi, sporcu icecegi, limonata, ayran, salgam, kola. Beer, non-alcoholic beverages, mineral water, spring water, soda, fruit juice, orange juice, soft drinks, carbonated drinks, energy drinks, sports drinks, lemonade.",
    33: "Alkollu icecekler, alkol, sarap, raki, viski, votka, cin, rom, konyak, brendi, sampanya, likor, tekila, bira harici alkollu icecekler, icki, damitik icecek, elma sarabi. Alcoholic beverages, alcohol, wine, raki, whiskey, vodka, gin, rum, cognac, brandy, champagne, liqueur, tequila, spirits, distilled beverages, cider.",
    34: "Tutun, sigara, puro, pipo, nargile, elektronik sigara, cakmak, kibrit, kuluk, tutun kutulari, sigara kagidi. Tobacco, cigarettes, cigars, pipes, hookah, e-cigarettes, lighters, matches, ashtrays, tobacco cases, rolling papers.",
    35: "Reklamcilik, pazarlama, is yonetimi, is danismanligi, perakende, toptan satis, ithalat, ihracat, ticari yonetim, muhasebe, insan kaynaklari, ofis hizmetleri, mallarin bir araya getirilmesi, ticaret, e-ticaret, market, magaza isletme. Advertising, marketing, business management, business consulting, retail, wholesale, import, export, trade management, accounting, human resources, office services, retail services, e-commerce, store management.",
    36: "Sigortacilik, bankacilik, finans, gayrimenkul, emlak, yatirim, kredi, para transferi, gumruk musavirligi, degerleme, komisyonculuk. Insurance, banking, finance, real estate, property, investment, credit, money transfer, customs brokerage, valuation, brokerage.",
    37: "Insaat, yapi, onarim, tadilat, restorasyon, tesisat, boya badana, elektrik tesisati, su tesisati, temizlik hizmetleri, dezenfeksiyon, hasere ilaclama, arac tamiri, ayakkabi tamiri, mobilya tamiri. Construction, building, repair, renovation, restoration, plumbing, painting, electrical installation, water installation, cleaning services, disinfection, pest control, vehicle repair, shoe repair, furniture repair.",
    38: "Telekomunikasyon, internet hizmeti, telefon hizmeti, radyo yayini, televizyon yayini, haberlesme, veri iletimi, mobil iletisim, yayincilik, haber ajansi. Telecommunications, internet services, telephone services, radio broadcasting, television broadcasting, communications, data transmission, mobile communications, broadcasting, news agency.",
    39: "Tasimacilik, nakliye, nakliyat, lojistik, depolama, kargo, kurye, dagitim, paketleme, deniz tasimaciligi, hava tasimaciligi, kara tasimaciligi, demiryolu, seyahat, tur organizasyonu, tasima, gonderi, posta, evden eve nakliyat. Transportation, shipping, logistics, storage, cargo, courier, delivery, distribution, packaging, sea transport, air transport, land transport, railway, travel, tour organization, moving services.",
    40: "Malzeme isleme, metal isleme, ahsap isleme, tekstil isleme, gida isleme, baski hizmetleri, matbaa, ciltleme, fotografik isleme, plastik isleme, geri donusum, enerji uretimi. Material treatment, metalworking, woodworking, textile processing, food processing, printing services, bookbinding, photographic processing, plastic processing, recycling, energy production.",
    41: "Egitim, egitim hizmetleri, ogretim, ogretmen, okul, universite, kurs, seminer, konferans, workshop, kocluk, egitim danismanligi, eglence, sinema, film, tiyatro, konser, muzik, spor etkinligi, spor egitimi, yayin, yayincilik, kitap, dergi, gazete, kutuphane, muze, sergi, festival, oyun salonu, dans, bale, sanat egitimi. Education, educational services, teaching, teacher, school, university, course, seminar, conference, workshop, coaching, tutoring, entertainment, cinema, film, theater, concert, music, sports events, sports training, publishing, books, magazines, newspapers, library, museum, exhibition, festival, arcade, dance, ballet, art education.",
    42: "Yazilim, yazilim gelistirme, bilgisayar programlama, web gelistirme, web sitesi tasarimi, web tasarim, internet sitesi, mobil uygulama gelistirme, uygulama gelistirme, bulut bilisim, SaaS, hosting, sunucu, IT hizmetleri, bilgi teknolojisi, bilisim, teknoloji, bilimsel arastirma, muhendislik, muhendislik hizmetleri, mimari tasarim, kalite kontrol, test hizmetleri, grafik tasarim, urun tasarimi, sanayi tasarimi, siber guvenlik, veri analizi, yapay zeka. Software, software development, computer programming, web development, website design, web design, mobile app development, app development, cloud computing, SaaS, hosting, server, IT services, information technology, technology, scientific research, engineering, architectural design, quality control, testing, graphic design, product design, industrial design, cybersecurity, data analysis, artificial intelligence.",
    43: "Restoran, lokanta, kafe, bar, otel, otel isletme, konaklama, pansiyon, apart otel, yiyecek icecek hizmetleri, catering, paket servis, pizzaci, pastane, bufe, meyhane, yemek servisi, otel rezervasyon, tatil koyu, motel, hostel, konuk evi, yemek pisirme, asci. Restaurant, cafe, bar, hotel, hotel management, accommodation, guesthouse, apart hotel, food and drink services, catering, takeaway, pizzeria, patisserie, buffet, tavern, food service, hotel reservation, resort, motel, hostel, cooking, chef.",
    44: "Tibbi hizmetler, saglik, hastane, klinik, doktor, dis hekimligi, eczane, psikoloji, guzellik salonu, masaj, kaplica, SPA, veteriner, hayvan bakimi, tarim hizmetleri, bahcecilik, peyzaj, ormancilik, cilt bakimi. Medical services, healthcare, hospital, clinic, doctor, dentistry, pharmacy, psychology, beauty salon, massage, spa, thermal baths, veterinary, animal care, agriculture services, gardening, landscaping, forestry, skincare.",
    45: "Hukuk hizmetleri, avukatlik, patent, marka tescili, fikri mulkiyet, lisanslama, guvenlik hizmetleri, ozel dedektif, kisisel hizmetler, cenaze hizmetleri, evlilik ajansi, bebek bakicisi, sosyal hizmetler. Legal services, law, attorney, patent, trademark registration, intellectual property, licensing, security services, private detective, personal services, funeral services, marriage agency, babysitting, social services.",
}

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", 5433)),
    database=os.getenv("DB_NAME", "trademark_db"),
    user=os.getenv("DB_USER", "turk_patent"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute(
    "SELECT class_number, description FROM nice_classes_lookup WHERE class_number <= 45 ORDER BY class_number"
)
rows = cur.fetchall()

from ai import get_text_embedding_cached

# Classes with known corrupted/wrong DB descriptions — don't append desc
BAD_DESC_CLASSES = {27, 35, 37, 41, 45}

updated = 0
for r in rows:
    cn = r["class_number"]
    desc = r["description"] or ""
    summary = CLASS_SUMMARIES.get(cn, "")

    # Summary only for corrupted classes; summary + truncated desc for clean ones
    if cn in BAD_DESC_CLASSES:
        enriched = summary
    else:
        enriched = summary + " " + desc[:200]

    emb = get_text_embedding_cached(enriched)
    emb_str = "[" + ",".join(map(str, emb)) + "]"

    cur.execute(
        "UPDATE nice_classes_lookup SET description_embedding = %s::halfvec, updated_at = NOW() WHERE class_number = %s",
        (emb_str, cn),
    )
    updated += 1

conn.commit()
print(f"Updated {updated} embeddings with comprehensive bilingual summaries")
conn.close()
